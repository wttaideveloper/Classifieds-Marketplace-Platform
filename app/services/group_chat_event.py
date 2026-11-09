"""Event groups: a group chat for the online registrants of one Event.

    manageable_events    the events the caller may start a group for (with how many registrants and groups each has)
    list_attendees       an event's invitation candidates, searchable and paged, each with the platform user id to invite
    list_providers       the verified Providers of the enterprise that can be added to the group
    event_groups         the groups already linked to an event
    assert_can_link      create_group: the event exists, belongs to the caller's enterprise, is open, and the caller is its Enterprise Admin
    check_invitees       everyone invited to an event group has an eligible registration for that event, or is a verified Provider
    pick_eligible        "invite everyone registered", capped at the free places
    assert_still_eligible  accepting an invitation: the registrant is still eligible (a refund after the invitation closes the door)

Who may manage an event group: the Enterprise Admin of the tenant that owns the event. Nobody else: not a Provider (assigned or
not), not a speaker or organiser typed into the event form, not the Super Admin. Providers can be invited as ordinary members.

Who may be invited as a registrant (all of these, checked on the server every time):
  * the registration is for this event, online (walk-ins are never part of Event Group Chat) and `confirmed` or `attended`
    (cancelled and no_show are not);
  * its payment is `paid`, or `free` (a free event, or a free ticket with nothing to pay); unpaid, pending, failed, cancelled,
    refund_requested and refunded are not (the payment state is derived from the paired order, app/utils/event_payments.py);
  * it carries a platform user id (EventRegistration.user_id: saved when the registrant registered, or recovered by email);
  * one person is one member however many registrations they have (deduplicated on the user id).
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from uuid import UUID

import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.chat_model import Conversation, ConversationParticipant
from app.models.group_chat_model import VerifiedProvider
from app.services import group_chat_common as common
from app.services.group_chat_training import _STATE_OF, _STATE_RANK, _name, _verified_ids

logger = logging.getLogger(__name__)

LINK_TYPE = "event"
CREATABLE_STATUSES = ("published", "completed")      # registrants exist once an event is published; a cancelled event gets no new group
ELIGIBLE_REGISTRATION = ("confirmed", "attended")
ELIGIBLE_PAYMENT = ("paid", "free")


# ---------------------------------------------------------------- the event and who may manage it

def _event_tenant(event) -> UUID | None:
    from app.repository.event_repo import event_owner_tenant_id

    try:
        raw = event_owner_tenant_id(event)
        return UUID(str(raw)) if raw else None
    except (TypeError, ValueError):
        return None


def _load_event(db: Session, event_id: UUID):
    from app.repository.event_repo import get_event_by_id

    event = get_event_by_id(db, event_id)
    if event is None or getattr(event, "is_deleted", False):
        raise HTTPException(status_code=404, detail="Event not found")
    return event


def assert_can_manage(db: Session, current_user: dict, event_id: UUID, access_token: str | None = None):
    """The event, or 403 / 404. Only the Enterprise Admin of the enterprise that owns the event."""
    caller_tenant = common.caller_tenant(db, current_user, access_token)
    if caller_tenant is None:
        raise HTTPException(status_code=403, detail="Authenticated tenant identity required to use an event for a group")
    if common.platform_role(current_user) != "admin":
        raise HTTPException(status_code=403, detail="Only the Enterprise Admin of the enterprise that owns an event can create or manage its groups")
    event = _load_event(db, event_id)
    owner = _event_tenant(event)
    if owner is None or owner != caller_tenant:
        raise HTTPException(status_code=403, detail="This event belongs to another enterprise")
    return event


def assert_can_link(db: Session, current_user: dict, event_id: UUID, access_token: str | None = None):
    """For create_group: the same checks, with the message of the group API when the event does not exist, and the event must be open."""
    try:
        event = assert_can_manage(db, current_user, event_id, access_token)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=422, detail="The linked event was not found")
        raise
    if (event.status or "") not in CREATABLE_STATUSES:
        raise HTTPException(status_code=409, detail="A group can be created for a published or completed event only")
    return event


# ---------------------------------------------------------------- eligibility (the one definition, in SQL)

def _payment_label(event):
    """The derived payment state of a registration (paid / free / unpaid / pending / ...), as a SQL expression. A registration
    without an order is free when the event is free or its ticket costs nothing, else unpaid."""
    from app.models.event_aux_models import EventOrder, EventRegistration
    from app.services.event_attendee_service import _payment_expression
    from app.services.event_service import _event_is_paid, _to_float

    base = _payment_expression(_event_is_paid(event))
    free_tickets = [str(t["id"]) for t in (event.ticket_types or []) if isinstance(t, dict) and t.get("id") is not None and _to_float(t.get("price")) <= 0]
    if free_tickets:
        return sa.case((sa.and_(EventOrder.id.is_(None), EventRegistration.ticket_type_id.in_(free_tickets)), "free"), else_=base)
    return base


def _online():
    from app.models.event_aux_models import EventRegistration

    return sa.func.coalesce(EventRegistration.registration_source, "online") != "walk_in"


def _eligible(label):
    from app.models.event_aux_models import EventRegistration

    return sa.and_(EventRegistration.status.in_(ELIGIBLE_REGISTRATION), label.in_(ELIGIBLE_PAYMENT))


def _select(event, *columns):
    from app.models.event_aux_models import EventRegistration
    from app.services.event_attendee_service import _with_orders

    return _with_orders(sa.select(*columns).select_from(EventRegistration), event.id)


def _eligible_registrations(db: Session, event, *extra) -> list:
    from app.models.event_aux_models import EventRegistration

    stmt = (
        _select(event, EventRegistration)
        .where(EventRegistration.event_id == event.id, _online(), _eligible(_payment_label(event)), *extra)
        .order_by(EventRegistration.created_at, EventRegistration.id)
    )
    return list(db.execute(stmt).scalars().all())


def reason_for(registration_status: str | None, payment: str | None) -> str | None:
    """Why a registration cannot be invited, or None when it can (before the account and group checks)."""
    status = (registration_status or "").lower()
    if status == "cancelled":
        return "registration_cancelled"
    if status == "no_show":
        return "registration_no_show"
    if status not in ELIGIBLE_REGISTRATION:
        return "registration_not_active"
    if payment in ELIGIBLE_PAYMENT:
        return None
    return f"payment_{payment or 'unpaid'}"


# ---------------------------------------------------------------- the events to choose from

def _tenant_filter(tenant: UUID):
    from app.models.enterprise_model import Enterprise
    from app.models.event_model import Event

    return sa.or_(
        Event.tenant_id == tenant,
        sa.and_(Event.tenant_id.is_(None), Event.enterprise.has(Enterprise.tenant_id == tenant)),
    )


def manageable_events(
    db: Session, current_user: dict, access_token: str | None = None, *, q: str | None = None, status: str | None = None,
    page: int = 1, page_size: int = 20,
) -> dict:
    from app.models.event_aux_models import EventRegistration
    from app.models.event_model import Event

    tenant = common.caller_tenant(db, current_user, access_token)
    if tenant is None:
        raise HTTPException(status_code=403, detail="Authenticated tenant identity required to use an event for a group")
    if common.platform_role(current_user) != "admin":
        raise HTTPException(status_code=403, detail="Only the Enterprise Admin of the enterprise that owns an event can create or manage its groups")
    query = db.query(Event).filter(Event.is_deleted.is_(False), _tenant_filter(tenant))
    if q and q.strip():
        query = query.filter(Event.title.ilike(f"%{q.strip()}%"))
    if status:
        query = query.filter(Event.status == status)
    total = query.count()
    rows = query.order_by(Event.start_date.desc().nullslast(), Event.id).offset((page - 1) * page_size).limit(page_size).all()
    ids = [e.id for e in rows]

    registered = dict(
        db.query(EventRegistration.event_id, sa.func.count(EventRegistration.id))
        .filter(EventRegistration.event_id.in_(ids), EventRegistration.status.in_(ELIGIBLE_REGISTRATION), _online())
        .group_by(EventRegistration.event_id).all()
    ) if ids else {}
    groups = dict(
        db.query(Conversation.context_id, sa.func.count(Conversation.id))
        .filter(Conversation.conversation_type == common.GROUP_TYPE, Conversation.context_type == LINK_TYPE,
                Conversation.context_id.in_(ids), Conversation.is_deleted.is_(False), Conversation.tenant_id == tenant)
        .group_by(Conversation.context_id).all()
    ) if ids else {}
    return {
        "items": [
            {
                "id": e.id, "title": e.title, "status": e.status, "delivery_mode": e.delivery_mode, "start_date": e.start_date,
                "end_date": e.end_date, "registered_count": int(registered.get(e.id, 0)), "group_count": int(groups.get(e.id, 0)),
                "can_create_group": (e.status or "") in CREATABLE_STATUSES,
                "reason": None if (e.status or "") in CREATABLE_STATUSES else "event_not_open",
            }
            for e in rows
        ],
        "total": total, "page": page, "page_size": page_size,
    }


# ---------------------------------------------------------------- the registrants of an event

def _group_states(db: Session, event_id: UUID, tenant: UUID, user_ids, group_id: UUID | None) -> dict[UUID, tuple[str, list[UUID]]]:
    """{user id: (best state, [group ids])} over the event's groups (or just `group_id`)."""
    ids = [u for u in user_ids if u]
    if not ids:
        return {}
    query = (
        db.query(ConversationParticipant.user_id, ConversationParticipant.membership_status, Conversation.id)
        .join(Conversation, Conversation.id == ConversationParticipant.conversation_id)
        .filter(Conversation.conversation_type == common.GROUP_TYPE, Conversation.context_type == LINK_TYPE,
                Conversation.context_id == event_id, Conversation.is_deleted.is_(False), Conversation.tenant_id == tenant,
                ConversationParticipant.user_id.in_(ids))
    )
    if group_id is not None:
        query = query.filter(Conversation.id == group_id)
    out: dict[UUID, tuple[str, list[UUID]]] = {}
    for user_id, membership, conversation_id in query.all():
        state = _STATE_OF.get(membership, "none")
        best, found = out.get(user_id, ("none", []))
        if state in ("member", "invited"):
            found.append(conversation_id)
            out[user_id] = (state if _STATE_RANK[state] > _STATE_RANK[best] else best, found)
        elif _STATE_RANK[state] > _STATE_RANK[best]:
            out[user_id] = (state, found)
    return out


def _check_group_of_event(db: Session, event_id: UUID, tenant: UUID, group_id: UUID | None) -> None:
    if group_id is None:
        return
    found = (
        db.query(Conversation.id)
        .filter(Conversation.id == group_id, Conversation.conversation_type == common.GROUP_TYPE,
                Conversation.context_type == LINK_TYPE, Conversation.context_id == event_id, Conversation.tenant_id == tenant,
                Conversation.is_deleted.is_(False)).first()
    )
    if found is None:
        raise HTTPException(status_code=404, detail="That group is not a group of this event")


def list_attendees(
    db: Session, current_user: dict, event_id: UUID, access_token: str | None = None, *, q: str | None = None,
    eligible_only: bool = True, registration_status: str | None = None, payment_status: str | None = None,
    not_in_group: bool = False, group_id: UUID | None = None, page: int = 1, page_size: int = 50,
) -> dict:
    from app.models.event_aux_models import EventOrder, EventRegistration
    from app.services.event_attendee_service import _escape_like
    from app.services.event_registration_identity import resolve_user_ids

    event = assert_can_manage(db, current_user, event_id, access_token)
    tenant = common.caller_tenant(db, current_user, access_token)
    _check_group_of_event(db, event.id, tenant, group_id)
    label = _payment_label(event)
    base = [EventRegistration.event_id == event.id, _online()]

    def count(*where) -> int:
        return db.execute(_select(event, sa.func.count(EventRegistration.id)).where(*base, *where)).scalar() or 0

    eligible_total = count(_eligible(label))
    walk_in_excluded = db.query(sa.func.count(EventRegistration.id)).filter(
        EventRegistration.event_id == event.id, EventRegistration.registration_source == "walk_in").scalar() or 0

    where = list(base)
    if registration_status:
        where.append(EventRegistration.status == registration_status)
    if payment_status:
        where.append(label == payment_status)
    if eligible_only and not registration_status and not payment_status:
        where.append(_eligible(label))
    if q and q.strip():
        like = f"%{_escape_like(q.strip())}%"
        where.append(sa.or_(EventRegistration.participant_name.ilike(like, escape="\\"), EventRegistration.participant_email.ilike(like, escape="\\")))
    if not_in_group:
        taken = (
            db.query(ConversationParticipant.user_id)
            .join(Conversation, Conversation.id == ConversationParticipant.conversation_id)
            .filter(Conversation.conversation_type == common.GROUP_TYPE, Conversation.context_type == LINK_TYPE,
                    Conversation.context_id == event.id, Conversation.is_deleted.is_(False),
                    ConversationParticipant.membership_status.in_(("active", "invited")))
        )
        if group_id is not None:
            taken = taken.filter(Conversation.id == group_id)
        where.append(sa.or_(EventRegistration.user_id.is_(None), ~EventRegistration.user_id.in_(taken)))
    total = count(*where[len(base):])
    rows = db.execute(
        _select(event, EventRegistration, label.label("payment_state")).where(*where)
        .order_by(sa.func.lower(EventRegistration.participant_name), EventRegistration.created_at, EventRegistration.id)
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    registrations = [r[0] for r in rows]
    resolve_user_ids(db, registrations, event, access_token)
    states = _group_states(db, event.id, tenant, [r.user_id for r in registrations], group_id)

    items, seen = [], set()
    for reg, payment in ((r[0], r[1]) for r in rows):
        reason = reason_for(reg.status, payment)
        state, group_ids = states.get(reg.user_id, ("none", [])) if reg.user_id else ("none", [])
        if reason is None and not reg.user_id:
            reason = "no_account"
        elif reason is None and reg.user_id in seen:
            reason = "duplicate_registration"
        elif reason is None and group_id is not None and state == "member":
            reason = "already_member"
        elif reason is None and group_id is not None and state == "invited":
            reason = "already_invited"
        if reason is None and reg.user_id:
            seen.add(reg.user_id)
        items.append({
            "registration_id": reg.id, "name": _name(reg.participant_name), "email": reg.participant_email,
            "registration_status": reg.status, "payment_status": payment, "user_id": reg.user_id,
            "can_invite": reason is None, "reason": reason, "group_state": state, "group_ids": group_ids,
            "registered_at": reg.created_at,
        })
    return {
        "event_id": event.id, "event_title": event.title, "event_status": event.status, "items": items, "total": total, "page": page,
        "page_size": page_size, "eligible_total": eligible_total, "walk_in_excluded": int(walk_in_excluded),
        "max_members": common.max_members("program", LINK_TYPE),
    }


def registrant_names(db: Session, conversation, user_ids) -> dict:
    """{user id: name on their registration} for an event group's pending invitees, newest registration first. Never an email."""
    from app.models.event_aux_models import EventRegistration

    ids = [u for u in user_ids if u]
    if not ids or conversation.context_type != LINK_TYPE or not conversation.context_id:
        return {}
    names: dict = {}
    for user_id, name in (
        db.query(EventRegistration.user_id, EventRegistration.participant_name)
        .filter(EventRegistration.event_id == conversation.context_id, EventRegistration.user_id.in_(ids))
        .order_by(EventRegistration.created_at.desc()).all()
    ):
        clean = _name(name)
        if clean and "@" not in clean:
            names.setdefault(user_id, clean)
    return names


# ---------------------------------------------------------------- the Providers to add

def list_providers(db: Session, current_user: dict, event_id: UUID, access_token: str | None = None, *, q: str | None = None) -> dict:
    event = assert_can_manage(db, current_user, event_id, access_token)
    tenant = common.caller_tenant(db, current_user, access_token)
    rows = (
        db.query(VerifiedProvider)
        .filter(VerifiedProvider.status == "verified", VerifiedProvider.tenant_id == tenant)
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
        items.append({"user_id": r.user_id, "name": name, "registration_number": r.registration_number, "is_me": r.user_id == me})
    items.sort(key=lambda i: (i["name"] or "~").lower())
    return {"event_id": event.id, "items": items, "total": len(items)}


# ---------------------------------------------------------------- the groups of an event

def event_groups(db: Session, current_user: dict, event_id: UUID, access_token: str | None = None) -> dict:
    """Every group of the event, for its Enterprise Admin (details only, never the messages of a group they are not in)."""
    from app.services.group_chat_service import group_dict

    event = assert_can_manage(db, current_user, event_id, access_token)
    tenant = common.caller_tenant(db, current_user, access_token)
    me = common.user_uuid(current_user)
    rows = (
        db.query(Conversation)
        .filter(Conversation.conversation_type == common.GROUP_TYPE, Conversation.context_type == LINK_TYPE,
                Conversation.context_id == event.id, Conversation.tenant_id == tenant, Conversation.is_deleted.is_(False))
        .order_by(Conversation.created_at.desc()).all()
    )
    items = []
    for c in rows:
        member = common.get_member(db, c.id, me)
        member = member if member is not None and member.membership_status == "active" else None
        items.append(group_dict(db, c, member, context_name=event.title))
    return {"items": items, "total": len(items)}


def context_names(db: Session, conversations) -> dict[UUID, str]:
    """{conversation id: event title} for the event groups among `conversations` (one query)."""
    from app.models.event_model import Event

    linked = {c.id: c.context_id for c in conversations if c.context_type == LINK_TYPE and c.context_id}
    if not linked:
        return {}
    titles = dict(db.query(Event.id, Event.title).filter(Event.id.in_(set(linked.values()))).all())
    return {cid: titles[eid] for cid, eid in linked.items() if eid in titles}


# ---------------------------------------------------------------- who may be invited

def _reasons(db: Session, event, user_ids) -> dict[str, str]:
    from app.models.event_aux_models import EventRegistration

    out: dict[str, str] = {}
    rows = db.execute(
        _select(event, EventRegistration, _payment_label(event).label("payment_state"))
        .where(EventRegistration.event_id == event.id, _online(), EventRegistration.user_id.in_(list(user_ids)))
        .order_by(EventRegistration.created_at.desc())
    ).all()
    for reg, payment in ((r[0], r[1]) for r in rows):
        out.setdefault(str(reg.user_id), reason_for(reg.status, payment) or "not_registered")
    return {str(u): out.get(str(u), "not_registered") for u in user_ids}


def check_invitees(db: Session, event, tenant: UUID, user_ids, access_token: str | None = None) -> None:
    """422 unless every invitee has an eligible registration for this event or is a verified Provider of the enterprise.
    A registration whose user id is not saved yet gets it looked up first, so inviting by id works before the roster was opened."""
    from app.models.event_aux_models import EventRegistration
    from app.services.event_registration_identity import resolve_user_ids

    ids = {u for u in user_ids if u}
    if not ids:
        return

    def split():
        attendees = {r.user_id for r in _eligible_registrations(db, event, EventRegistration.user_id.in_(ids))}
        return attendees, _verified_ids(db, ids - attendees, tenant)

    attendees, providers = split()
    if ids - attendees - providers:
        unresolved = _eligible_registrations(db, event, EventRegistration.user_id.is_(None))
        if unresolved:
            resolve_user_ids(db, unresolved, event, access_token)
            attendees, providers = split()
    bad = ids - attendees - providers
    if bad:
        raise HTTPException(status_code=422, detail={
            "message": "These people do not have an eligible registration for this event and are not verified providers of the enterprise, so they cannot be invited.",
            "code": "INVITEE_NOT_ELIGIBLE",
            "invalid_user_ids": sorted(str(u) for u in bad),
            "reasons": _reasons(db, event, bad),
        })


def assert_still_eligible(db: Session, event, user_id: UUID, tenant: UUID) -> None:
    """Accepting an invitation: a registrant whose registration was cancelled, refunded or never paid since the invitation was
    sent cannot join. A verified Provider is not a registrant and is not checked."""
    from app.models.event_aux_models import EventRegistration

    if _verified_ids(db, {user_id}, tenant):
        return
    if _eligible_registrations(db, event, EventRegistration.user_id == user_id):
        return
    raise HTTPException(status_code=409, detail="This invitation is no longer valid: your registration for the event is not active or its payment is not complete")


def assert_invitee_still_eligible(db: Session, conversation: Conversation, user_id: UUID) -> None:
    """For accept_invitation: nothing for other groups, and nothing if the event no longer exists."""
    if conversation.context_type != LINK_TYPE or not conversation.context_id or conversation.tenant_id is None:
        return
    from app.repository.event_repo import get_event_by_id

    event = get_event_by_id(db, conversation.context_id)
    if event is not None:
        assert_still_eligible(db, event, user_id, conversation.tenant_id)


def pick_eligible(
    db: Session, conversation: Conversation | None, event, inviter_id: UUID, access_token: str | None, *, limit: int,
    exclude=(), already_taken=(),
) -> tuple[list, dict]:
    """Eligible registrants to invite, oldest registration first: one per person, with a chat account, not the inviter, not excluded
    and not already active or invited in this group. At most `limit`. Returns (invitees, stats)."""
    from app.services.event_registration_identity import resolve_user_ids

    skip = {inviter_id, *exclude, *already_taken}
    if conversation is not None:   # None while the group is still being created
        skip |= {
            r[0] for r in db.query(ConversationParticipant.user_id)
            .filter(ConversationParticipant.conversation_id == conversation.id,
                    ConversationParticipant.membership_status.in_(("active", "invited"))).all()
        }
    rows = _eligible_registrations(db, event)
    resolve_user_ids(db, rows, event, access_token)
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
