"""Who a Training enrolment belongs to, as an application user id (``TrainingEnrolment.user_id``).

That id is what lets enrolment notifications (accepted / rejected / ...) reach the learner's feed, so it is
saved wherever an enrolment or waitlist entry is created, and recovered — safely — where it is missing:

* ``resolve_enrolling_user_id`` — at creation: is the authenticated caller the person being enrolled?
  Decided on the caller's verified identity, not on the token's email claim alone (which can be absent or
  spelled differently): the claim, then the Auth service's ``/auth/me`` email for that token.
* ``resolve_learner_user_id`` — for an existing row with no user id, never from user input: the same
  email on the person's other rows that do have one, then the Auth service's tenant user listing.
* ``link_caller_enrolments`` — a logged-in learner's own request links the old rows that carry *their*
  verified email. This is the only way to reach a learner who belongs to no tenant.
* ``backfill_user_ids`` — the same inference in bulk, dry-run by default (scripts/backfill_training_enrolment_user_ids.py).
"""
import logging
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

STAFF_ROLES = ("admin", "provider", "super_admin")


def _email(value) -> str:
    return str(value or "").strip().lower()


def _as_uuid(value) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def caller_verified_email(current_user: dict | None, access_token: str | None) -> str | None:
    """The authenticated caller's email: from the Auth service's profile for this token when it has one
    (rejected if the profile says it is unverified), otherwise the token's own email claim."""
    from app.services.invigorate_auth_client import fetch_profile_email

    claim = _email((current_user or {}).get("email"))
    claim = claim if "@" in claim else ""
    profile_email, verified = fetch_profile_email(access_token)
    if profile_email:
        return profile_email if verified else None
    return claim or None


def resolve_enrolling_user_id(current_user: dict | None, participant_email: str | None, access_token: str | None = None) -> UUID | None:
    """The caller's application user id when the caller *is* the person being enrolled, else None
    (staff enrolling someone else, group members). Not just "emails match": see module docstring."""
    caller_id = _as_uuid((current_user or {}).get("id"))
    if caller_id is None:
        return None
    target = _email(participant_email)
    claim = _email((current_user or {}).get("email"))
    if target and claim and target == claim:
        return caller_id

    from app.services.invigorate_auth_client import fetch_profile_email

    profile_email, verified = fetch_profile_email(access_token)
    if profile_email and verified and (not target or target == profile_email):
        return caller_id
    known_caller_email = bool(("@" in claim) or profile_email)
    if known_caller_email:
        return None  # we can see who the caller is, and it is a different person
    # Nothing to compare against: a learner enrolling is, by default, enrolling themselves. Staff are not.
    return caller_id if (current_user or {}).get("role") not in STAFF_ROLES else None


# --- recovering a missing user id -----------------------------------------------------------

def _tenant_of(db: Session, training) -> UUID | None:
    tenant = getattr(training, "tenant_id", None)
    if not tenant and getattr(training, "enterprise_id", None):
        from app.models.enterprise_model import Enterprise

        enterprise = db.query(Enterprise).filter(Enterprise.id == training.enterprise_id).first()
        tenant = getattr(enterprise, "tenant_id", None)
    return _as_uuid(tenant)


def _from_other_rows(db: Session, email: str) -> UUID | None:
    """Same person (same email) on another enrolment / waitlist row that already carries a user id."""
    from app.models.training_model import TrainingEnrolment, TrainingWaitlist

    for model in (TrainingEnrolment, TrainingWaitlist):
        row = (
            db.query(model.user_id)
            .filter(func.lower(model.participant_email) == email, model.user_id.isnot(None))
            .order_by(model.created_at.desc())
            .first()
        )
        if row and row[0]:
            return row[0]
    return None


def _from_tenant_listing(db: Session, training, email: str, access_token: str | None) -> UUID | None:
    """The Auth service's member list of the training's tenant, matched on email. Only finds people who
    belong to that tenant; a learner who joined none is reached by link_caller_enrolments instead."""
    if training is None:
        return None
    tenant = _tenant_of(db, training)
    if tenant is None:
        return None
    # the same lookup (and id rules) the admin recipient resolution uses
    from app.services import event_notification_service as members

    for user in members._lookup(members.list_tenant_users, tenant, access_token=access_token):
        if any(_email(value.get("email")) == email for value in members._nested_values(user)):
            found = members._user_id(user)
            if found:
                return found
    return None


def resolve_learner_user_id(db: Session, row, training=None, access_token: str | None = None, persist: bool = True) -> UUID | None:
    """The learner's application user id for an enrolment (or waitlist) row, recovering it if the row has
    none. Never trusts request input; a recovered id is saved on the row when ``persist``."""
    if getattr(row, "user_id", None):
        return row.user_id
    email = _email(getattr(row, "participant_email", None))
    if not email:
        return None
    found = _from_other_rows(db, email) or _from_tenant_listing(db, training, email, access_token)
    if found and persist:
        row.user_id = found
        db.commit()
    return found


def link_caller_enrolments(db: Session, current_user: dict | None, access_token: str | None = None) -> int:
    """A logged-in learner's own request attaches their application user id to every older enrolment /
    waitlist row carrying *their* verified email. Returns the number of rows linked."""
    from app.models.training_model import TrainingEnrolment, TrainingWaitlist

    caller_id = _as_uuid((current_user or {}).get("id"))
    email = caller_verified_email(current_user, access_token)
    if caller_id is None or not email:
        return 0
    linked = 0
    for model in (TrainingEnrolment, TrainingWaitlist):
        linked += (
            db.query(model)
            .filter(func.lower(model.participant_email) == email, model.user_id.is_(None))
            .update({"user_id": caller_id}, synchronize_session=False)
        )
    if linked:
        db.commit()
    return linked


def backfill_user_ids(db: Session, *, apply: bool = False, access_token: str | None = None) -> dict:
    """Resolve user ids for every enrolment / waitlist row that has none, from the other rows of the same
    email and the tenants' member lists. Dry run unless ``apply``."""
    from app.models.training_model import Training, TrainingEnrolment, TrainingWaitlist

    counts = {"checked": 0, "resolved": 0, "unresolved": 0, "written": 0}
    trainings: dict = {}
    for model in (TrainingEnrolment, TrainingWaitlist):
        for row in db.query(model).filter(model.user_id.is_(None)).all():
            counts["checked"] += 1
            training = trainings.get(row.training_id)
            if training is None:
                training = trainings[row.training_id] = db.get(Training, row.training_id)
            found = resolve_learner_user_id(db, row, training, access_token, persist=False)
            if not found:
                counts["unresolved"] += 1
                continue
            counts["resolved"] += 1
            if apply:
                row.user_id = found
                counts["written"] += 1
    if apply and counts["written"]:
        db.commit()
    return counts
