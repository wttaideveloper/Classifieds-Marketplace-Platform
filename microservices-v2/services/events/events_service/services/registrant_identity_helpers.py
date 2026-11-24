"""Identity-from-email helpers events shares with learning (copied from app/services/training_learner_identity.py
and app/services/group_chat_training.py). They use only the identity provider, the enterprise replica and
events' own recipient lookup; ``training`` here is any record with ``enterprise_id`` (an Event)."""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


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
    from mskit.auth.invigorate_auth_client import fetch_profile_email

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

    from mskit.auth.invigorate_auth_client import fetch_profile_email

    profile_email, verified = fetch_profile_email(access_token)
    if profile_email and verified and (not target or target == profile_email):
        return caller_id
    known_caller_email = bool(("@" in claim) or profile_email)
    if known_caller_email:
        return None  # we can see who the caller is, and it is a different person
    # Nothing to compare against: a learner enrolling is, by default, enrolling themselves. Staff are not.
    return caller_id if (current_user or {}).get("role") not in STAFF_ROLES else None


def _tenant_of(db: Session, training) -> UUID | None:
    tenant = getattr(training, "tenant_id", None)
    if not tenant and getattr(training, "enterprise_id", None):
        from events_service.models.enterprise_model import Enterprise  # read-only replica

        enterprise = db.query(Enterprise).filter(Enterprise.id == training.enterprise_id).first()
        tenant = getattr(enterprise, "tenant_id", None)
    return _as_uuid(tenant)


def _from_tenant_listing(db: Session, training, email: str, access_token: str | None) -> UUID | None:
    """The Auth service's member list of the training's tenant, matched on email. Only finds people who
    belong to that tenant; a learner who joined none is reached by link_caller_enrolments instead."""
    if training is None:
        return None
    tenant = _tenant_of(db, training)
    if tenant is None:
        return None
    # the same lookup (and id rules) the admin recipient resolution uses
    from events_service.services import event_notification_service as members

    for user in members._lookup(members.list_tenant_users, tenant, access_token=access_token):
        if any(_email(value.get("email")) == email for value in members._nested_values(user)):
            found = members._user_id(user)
            if found:
                return found
    return None


class _EmailIndex:
    """email -> user id from the clinic's member list, loaded once per request and only if a row needs it."""

    def __init__(self, db: Session, training, access_token: str | None):
        self._db, self._training, self._token, self._index = db, training, access_token, None

    def get(self, email: str) -> UUID | None:
        if self._index is None:
            self._index = {}
            try:
                from events_service.services import event_notification_service as members
                from events_service.services.registrant_identity_helpers import _tenant_of

                tenant = _tenant_of(self._db, self._training)
                if tenant is not None:
                    for user in members._lookup(members.list_tenant_users, tenant, access_token=self._token):
                        found = members._user_id(user)
                        if not found:
                            continue
                        for value in members._nested_values(user):
                            mail = str(value.get("email") or "").strip().lower()
                            if mail:
                                self._index.setdefault(mail, found)
            except Exception:
                logger.exception("could not load the clinic's members to find learner accounts")
        return self._index.get(email)

