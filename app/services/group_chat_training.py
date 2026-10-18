"""Training groups: a group chat for the learners of one Training.

    manageable_trainings   the trainings the caller may start a group for (with how many learners and groups each has)
    list_learners          a training's enrolled learners, searchable and paged, each with the id to invite
    list_providers         the verified Providers of the clinic that can be added to the group
    training_groups        the groups already linked to a training
    assert_can_link        create_group: the training exists, belongs to the caller's clinic and the caller may manage it
    check_invitees         everyone invited to a training group is an enrolled learner of that training or a verified Provider
    pick_eligible          "invite everyone enrolled", capped at the free places

Who may manage a training: an Enterprise Admin of the clinic that owns it; a verified Provider of that clinic who is the
training's assigned instructor (`Training.instructor_id`), or any verified Provider of the clinic when
GROUP_PROVIDER_REQUIRES_TRAINING_ASSIGNMENT is off. The instructor *name* typed in the training form is information only
and never makes anyone a member.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from uuid import UUID

import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.chat_model import Conversation, ConversationParticipant
from app.models.group_chat_model import VerifiedProvider
from app.services import group_chat_common as common

logger = logging.getLogger(__name__)

LINK_TYPE = "training"
# Enrolment statuses of a learner who is really in the training (the same idea as the review rule, plus attended).
ELIGIBLE_STATUSES = ("enrolled", "active", "completed", "approved", "attended")
_STATE_RANK = {"member": 5, "invited": 4, "declined": 3, "removed": 2, "left": 1, "none": 0}
_STATE_OF = {"active": "member", "invited": "invited", "declined": "declined", "removed": "removed", "left": "left"}


# ---------------------------------------------------------------- the training and who may manage it

def _training_tenant(training) -> UUID | None:
    raw = (training.enterprise.tenant_id if getattr(training, "enterprise", None) else None) or training.tenant_id
    try:
        return UUID(str(raw)) if raw else None
    except (TypeError, ValueError):
        return None


def _load_training(db: Session, training_id: UUID):
    from app.repository.training_repo import get_training_by_id

    training = get_training_by_id(db, training_id)
    if training is None or getattr(training, "is_deleted", False):
        raise HTTPException(status_code=404, detail="Training not found")
    return training


def assert_can_manage(db: Session, current_user: dict, training_id: UUID, access_token: str | None = None):
    """The training, or 403 / 404. Only the clinic's Enterprise Admin and verified Providers of the clinic (assigned to
    the training, unless that rule is off) may use a training for a group."""
    common.assert_can_create_groups(db, current_user)     # admin, or a verified provider; never a learner / Super Admin
    caller_tenant = common.caller_tenant(db, current_user, access_token)
    if caller_tenant is None:
        raise HTTPException(status_code=403, detail="Authenticated tenant identity required to use a training for a group")
    training = _load_training(db, training_id)
    owner = _training_tenant(training)
    if owner is None or owner != caller_tenant or (training.tenant_id and UUID(str(training.tenant_id)) != caller_tenant):
        raise HTTPException(status_code=403, detail="This training belongs to another clinic")
    if common.platform_role(current_user) == "provider" and settings.GROUP_PROVIDER_REQUIRES_TRAINING_ASSIGNMENT:
        if training.instructor_id is None or UUID(str(training.instructor_id)) != common.user_uuid(current_user):
            raise HTTPException(status_code=403, detail="You can start a group only for a training you are assigned to")
    return training


def assert_can_link(db: Session, current_user: dict, training_id: UUID, access_token: str | None = None):
    """For create_group: the same checks, with the message of the group API when the training does not exist."""
    try:
        return assert_can_manage(db, current_user, training_id, access_token)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=422, detail="The linked training was not found")
        raise


def _tenant_filter(tenant: UUID):
    from app.models.enterprise_model import Enterprise
    from app.models.training_model import Training

    return sa.or_(
        sa.and_(Training.tenant_id == tenant),
        sa.and_(Training.tenant_id.is_(None), Training.enterprise.has(Enterprise.tenant_id == tenant)),
    )


# ---------------------------------------------------------------- the trainings to choose from

def manageable_trainings(
    db: Session, current_user: dict, access_token: str | None = None, *, q: str | None = None, status: str | None = None,
    page: int = 1, page_size: int = 20,
) -> dict:
    from app.models.training_model import Training, TrainingEnrolment

    common.assert_can_create_groups(db, current_user)
    tenant = common.caller_tenant(db, current_user, access_token)
    if tenant is None:
        raise HTTPException(status_code=403, detail="Authenticated tenant identity required to use a training for a group")
    query = db.query(Training).filter(Training.is_deleted.is_(False), _tenant_filter(tenant))
    if common.platform_role(current_user) == "provider" and settings.GROUP_PROVIDER_REQUIRES_TRAINING_ASSIGNMENT:
        query = query.filter(Training.instructor_id == common.user_uuid(current_user))
    if q and q.strip():
        query = query.filter(Training.title.ilike(f"%{q.strip()}%"))
    if status:
        query = query.filter(Training.status == status)
    total = query.count()
    rows = query.order_by(Training.created_at.desc(), Training.id).offset((page - 1) * page_size).limit(page_size).all()
    ids = [t.id for t in rows]

    enrolled = dict(
        db.query(TrainingEnrolment.training_id, sa.func.count(TrainingEnrolment.id))
        .filter(TrainingEnrolment.training_id.in_(ids), TrainingEnrolment.status.in_(ELIGIBLE_STATUSES))
        .group_by(TrainingEnrolment.training_id).all()
    ) if ids else {}
    groups = dict(
        db.query(Conversation.context_id, sa.func.count(Conversation.id))
        .filter(Conversation.conversation_type == common.GROUP_TYPE, Conversation.context_type == LINK_TYPE,
                Conversation.context_id.in_(ids), Conversation.is_deleted.is_(False), Conversation.tenant_id == tenant)
        .group_by(Conversation.context_id).all()
    ) if ids else {}
    instructor_ids = {t.instructor_id for t in rows if t.instructor_id}
    verified = _verified_ids(db, instructor_ids, tenant)
    return {
        "items": [
            {
                "id": t.id, "title": t.title, "status": t.status, "delivery_mode": t.delivery_mode,
                "instructor_name": t.instructor_name, "instructor_user_id": t.instructor_id,
                "instructor_is_verified_provider": t.instructor_id in verified,
                "enrolled_count": int(enrolled.get(t.id, 0)), "group_count": int(groups.get(t.id, 0)), "created_at": t.created_at,
            }
            for t in rows
        ],
        "total": total, "page": page, "page_size": page_size,
    }


def _verified_ids(db: Session, user_ids, tenant: UUID) -> set[UUID]:
    ids = {i for i in user_ids if i}
    if not ids:
        return set()
    rows = (
        db.query(VerifiedProvider.user_id)
        .filter(VerifiedProvider.user_id.in_(ids), VerifiedProvider.status == "verified",
                sa.or_(VerifiedProvider.tenant_id == tenant, VerifiedProvider.tenant_id.is_(None)))
        .all()
    )
    return {r[0] for r in rows}


# ---------------------------------------------------------------- the learners of a training

class _EmailIndex:
    """email -> user id from the clinic's member list, loaded once per request and only if a row needs it."""

    def __init__(self, db: Session, training, access_token: str | None):
        self._db, self._training, self._token, self._index = db, training, access_token, None

    def get(self, email: str) -> UUID | None:
        if self._index is None:
            self._index = {}
            try:
                from app.services import event_notification_service as members
                from app.services.training_learner_identity import _tenant_of

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


def _resolve_user_ids(db: Session, rows, training, access_token: str | None) -> None:
    """Fill TrainingEnrolment.user_id where it is missing: another row with the same email, then the clinic's member
    list. The id is saved on the row, so the next call is free. Never from request input."""
    from app.services.training_learner_identity import _from_other_rows

    index = _EmailIndex(db, training, access_token)
    changed = False
    for row in rows:
        if row.user_id:
            continue
        email = str(row.participant_email or "").strip().lower()
        if not email:
            continue
        found = _from_other_rows(db, email) or index.get(email)
        if found:
            row.user_id = found
            changed = True
    if changed:
        db.commit()


def _group_states(db: Session, training_id: UUID, tenant: UUID, user_ids, group_id: UUID | None) -> dict[UUID, tuple[str, list[UUID]]]:
    """{user id: (best state, [group ids])} over the training's groups (or just `group_id`)."""
    ids = [u for u in user_ids if u]
    if not ids:
        return {}
    query = (
        db.query(ConversationParticipant.user_id, ConversationParticipant.membership_status, Conversation.id)
        .join(Conversation, Conversation.id == ConversationParticipant.conversation_id)
        .filter(Conversation.conversation_type == common.GROUP_TYPE, Conversation.context_type == LINK_TYPE,
                Conversation.context_id == training_id, Conversation.is_deleted.is_(False), Conversation.tenant_id == tenant,
                ConversationParticipant.user_id.in_(ids))
    )
    if group_id is not None:
        query = query.filter(Conversation.id == group_id)
    out: dict[UUID, tuple[str, list[UUID]]] = {}
    for user_id, membership, conversation_id in query.all():
        state = _STATE_OF.get(membership, "none")
        if state in ("member", "invited"):
            best, groups = out.get(user_id, ("none", []))
            groups.append(conversation_id)
            out[user_id] = (state if _STATE_RANK[state] > _STATE_RANK[best] else best, groups)
        else:
            best, groups = out.get(user_id, ("none", []))
            if _STATE_RANK[state] > _STATE_RANK[best]:
                out[user_id] = (state, groups)
    return out


def _check_group_of_training(db: Session, training_id: UUID, tenant: UUID, group_id: UUID | None) -> None:
    if group_id is None:
        return
    found = (
        db.query(Conversation.id)
        .filter(Conversation.id == group_id, Conversation.conversation_type == common.GROUP_TYPE,
                Conversation.context_type == LINK_TYPE, Conversation.context_id == training_id, Conversation.tenant_id == tenant,
                Conversation.is_deleted.is_(False)).first()
    )
    if found is None:
        raise HTTPException(status_code=404, detail="That group is not a group of this training")


def list_learners(
    db: Session, current_user: dict, training_id: UUID, access_token: str | None = None, *, q: str | None = None,
    enrolment_status: str | None = None, eligible_only: bool = True, not_in_group: bool = False, group_id: UUID | None = None,
    page: int = 1, page_size: int = 50,
) -> dict:
    from app.models.training_model import TrainingEnrolment

    training = assert_can_manage(db, current_user, training_id, access_token)
    tenant = common.caller_tenant(db, current_user, access_token)
    _check_group_of_training(db, training.id, tenant, group_id)
    base = db.query(TrainingEnrolment).filter(TrainingEnrolment.training_id == training.id)
    eligible_total = base.filter(TrainingEnrolment.status.in_(ELIGIBLE_STATUSES)).count()

    query = base
    if enrolment_status:
        query = query.filter(TrainingEnrolment.status == enrolment_status)
    elif eligible_only:
        query = query.filter(TrainingEnrolment.status.in_(ELIGIBLE_STATUSES))
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(sa.or_(TrainingEnrolment.participant_name.ilike(like), TrainingEnrolment.participant_email.ilike(like)))
    if not_in_group:
        taken = (
            db.query(ConversationParticipant.user_id)
            .join(Conversation, Conversation.id == ConversationParticipant.conversation_id)
            .filter(Conversation.conversation_type == common.GROUP_TYPE, Conversation.context_type == LINK_TYPE,
                    Conversation.context_id == training.id, Conversation.is_deleted.is_(False),
                    ConversationParticipant.membership_status.in_(("active", "invited")))
        )
        if group_id is not None:
            taken = taken.filter(Conversation.id == group_id)
        query = query.filter(sa.or_(TrainingEnrolment.user_id.is_(None), ~TrainingEnrolment.user_id.in_(taken)))
    total = query.count()
    rows = (
        query.order_by(sa.func.lower(TrainingEnrolment.participant_name), TrainingEnrolment.created_at, TrainingEnrolment.id)
        .offset((page - 1) * page_size).limit(page_size).all()
    )
    _resolve_user_ids(db, rows, training, access_token)
    states = _group_states(db, training.id, tenant, [r.user_id for r in rows], group_id)

    items = []
    for r in rows:
        state, group_ids = states.get(r.user_id, ("none", [])) if r.user_id else ("none", [])
        if r.status not in ELIGIBLE_STATUSES:
            can, reason = False, "not_eligible_status"
        elif not r.user_id:
            can, reason = False, "no_account"
        elif group_id is not None and state == "member":
            can, reason = False, "already_member"      # in THE group being filled
        elif group_id is not None and state == "invited":
            can, reason = False, "already_invited"
        else:
            # Without group_id the list is for a NEW group: a learner can be in several groups of one training, so
            # being in another group does not stop them being invited. group_state / group_ids still show where they are.
            can, reason = True, None
        items.append({
            "enrolment_id": r.id, "name": _name(r.participant_name), "email": r.participant_email, "enrolment_status": r.status,
            "user_id": r.user_id, "can_invite": can, "reason": reason, "group_state": state, "group_ids": group_ids,
            "enrolled_at": r.created_at,
        })
    return {
        "training_id": training.id, "training_title": training.title, "items": items, "total": total, "page": page,
        "page_size": page_size, "eligible_total": eligible_total, "max_members": common.max_members("program"),
    }


def enrolment_names(db: Session, conversation, user_ids) -> dict:
    """{user id: name on their enrolment} for a training group's pending invitees, newest enrolment first. Never an email."""
    from app.models.training_model import TrainingEnrolment

    ids = [u for u in user_ids if u]
    if not ids or conversation.context_type != LINK_TYPE or not conversation.context_id:
        return {}
    names: dict = {}
    for user_id, name in (
        db.query(TrainingEnrolment.user_id, TrainingEnrolment.participant_name)
        .filter(TrainingEnrolment.training_id == conversation.context_id, TrainingEnrolment.user_id.in_(ids))
        .order_by(TrainingEnrolment.created_at.desc()).all()
    ):
        clean = _name(name)
        if clean and "@" not in clean:
            names.setdefault(user_id, clean)
    return names


def _name(value) -> str | None:
    text = " ".join(str(value).split()) if value else ""
    return text or None


# ---------------------------------------------------------------- the Providers to add

def list_providers(db: Session, current_user: dict, training_id: UUID, access_token: str | None = None, *, q: str | None = None) -> dict:
    training = assert_can_manage(db, current_user, training_id, access_token)
    tenant = common.caller_tenant(db, current_user, access_token)
    rows = (
        db.query(VerifiedProvider)
        .filter(VerifiedProvider.status == "verified", sa.or_(VerifiedProvider.tenant_id == tenant, VerifiedProvider.tenant_id.is_(None)))
        .order_by(VerifiedProvider.verified_at.asc()).limit(500).all()
    )
    me = common.user_uuid(current_user)
    names = common.resolve_names(db, SimpleNamespace(tenant_id=tenant), [r.user_id for r in rows], current_user)
    needle = (q or "").strip().lower()
    items = []
    for r in rows:
        name = names.get(str(r.user_id))
        if needle and needle not in (name or "").lower() and needle not in (r.registration_number or "").lower():
            continue
        items.append({
            "user_id": r.user_id, "name": name, "registration_number": r.registration_number,
            "is_training_instructor": bool(training.instructor_id and r.user_id == training.instructor_id), "is_me": r.user_id == me,
        })
    items.sort(key=lambda i: (not i["is_training_instructor"], (i["name"] or "~").lower()))
    return {"training_id": training.id, "instructor_name": training.instructor_name, "items": items, "total": len(items)}


# ---------------------------------------------------------------- the groups of a training

def training_groups(db: Session, current_user: dict, training_id: UUID, access_token: str | None = None) -> dict:
    """The training's groups the caller may see: the ones they are in, or all of them for the clinic's Enterprise Admin
    (details only, never the messages)."""
    from app.services.group_chat_service import group_dict

    training = assert_can_manage(db, current_user, training_id, access_token)
    tenant = common.caller_tenant(db, current_user, access_token)
    me = common.user_uuid(current_user)
    rows = (
        db.query(Conversation)
        .filter(Conversation.conversation_type == common.GROUP_TYPE, Conversation.context_type == LINK_TYPE,
                Conversation.context_id == training.id, Conversation.tenant_id == tenant, Conversation.is_deleted.is_(False))
        .order_by(Conversation.created_at.desc()).all()
    )
    is_admin = common.platform_role(current_user) == "admin"
    items = []
    for c in rows:
        member = common.get_member(db, c.id, me)
        member = member if member is not None and member.membership_status == "active" else None
        if member is None and not is_admin:
            continue
        items.append(group_dict(db, c, member, context_name=training.title))
    return {"items": items, "total": len(items)}


def context_names(db: Session, conversations) -> dict[UUID, str]:
    """{conversation id: training title} for the training groups among `conversations` (one query)."""
    from app.models.training_model import Training

    linked = {c.id: c.context_id for c in conversations if c.context_type == LINK_TYPE and c.context_id}
    if not linked:
        return {}
    titles = dict(db.query(Training.id, Training.title).filter(Training.id.in_(set(linked.values()))).all())
    return {cid: titles[tid] for cid, tid in linked.items() if tid in titles}


# ---------------------------------------------------------------- who may be invited

def check_invitees(db: Session, training, tenant: UUID, user_ids, access_token: str | None = None) -> None:
    """422 unless every invitee is an eligible enrolled learner of this training or a verified Provider of the clinic.
    A learner whose enrolment has no user id yet gets it looked up first, so inviting by id works before the roster
    was ever opened."""
    from app.models.training_model import TrainingEnrolment

    ids = {u for u in user_ids if u}
    if not ids:
        return

    def split():
        learners = {
            r[0] for r in db.query(TrainingEnrolment.user_id)
            .filter(TrainingEnrolment.training_id == training.id, TrainingEnrolment.user_id.in_(ids),
                    TrainingEnrolment.status.in_(ELIGIBLE_STATUSES)).all()
        }
        return learners, _verified_ids(db, ids - learners, tenant)

    learners, providers = split()
    if ids - learners - providers:
        unresolved = (
            db.query(TrainingEnrolment)
            .filter(TrainingEnrolment.training_id == training.id, TrainingEnrolment.user_id.is_(None),
                    TrainingEnrolment.status.in_(ELIGIBLE_STATUSES)).all()
        )
        if unresolved:
            _resolve_user_ids(db, unresolved, training, access_token)
            learners, providers = split()
    bad = sorted(str(u) for u in ids - learners - providers)
    if bad:
        raise HTTPException(status_code=422, detail={
            "message": "These people are not enrolled in this training and are not verified providers of the clinic, so they cannot be invited.",
            "invalid_user_ids": bad,
        })


def pick_eligible(
    db: Session, conversation: Conversation | None, training, inviter_id: UUID, access_token: str | None, *, limit: int,
    exclude=(), already_taken=(),
) -> tuple[list, dict]:
    """Eligible enrolled learners to invite, oldest enrolment first: the ones with a chat account, not the inviter, not
    excluded and not already active or invited in this group. At most `limit`. Returns (invitees, stats)."""
    from app.models.training_model import TrainingEnrolment

    skip = {inviter_id, *exclude, *already_taken}
    if conversation is not None:   # None while the group is still being created
        skip |= {
            r[0] for r in db.query(ConversationParticipant.user_id)
            .filter(ConversationParticipant.conversation_id == conversation.id,
                    ConversationParticipant.membership_status.in_(("active", "invited"))).all()
        }
    rows = (
        db.query(TrainingEnrolment)
        .filter(TrainingEnrolment.training_id == training.id, TrainingEnrolment.status.in_(ELIGIBLE_STATUSES))
        .order_by(TrainingEnrolment.created_at, TrainingEnrolment.id).all()
    )
    _resolve_user_ids(db, rows, training, access_token)
    chosen, seen, no_account, left_out = [], set(), 0, 0
    for r in rows:
        if not r.user_id:
            no_account += 1
            continue
        if r.user_id in skip or r.user_id in seen:
            continue
        seen.add(r.user_id)
        if len(chosen) >= max(limit, 0):
            left_out += 1
            continue
        chosen.append(SimpleNamespace(user_id=r.user_id, display_name=None, is_minor=False))
    return chosen, {"no_account": no_account, "not_invited_group_full": left_out}
