"""Event Discussions: one discussion per event, for its registered participants and its Enterprise Admins, with AI moderation.

Who may do what:
  participant       a registered (confirmed or attended) attendee: read, post, reply, edit/appeal their own blocked messages
  enterprise_admin  the Enterprise Admin of the event's owning tenant: all of the above, announcements, and approve / delete flagged messages
  super_admin       read only: the discussion, the review lists and the moderation history, across enterprises. Never posts, replies or decides.
Every message, an Enterprise Admin's too, is judged by the AI before anyone else sees it (see event_discussion_moderation). If the AI cannot
answer, the message stays unpublished (`pending_moderation`) and is judged again by the background job.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.exceptions.problem import problem
from app.models.event_aux_models import EventRegistration
from app.models.event_discussion_model import EventDiscussionMember, EventDiscussionMessage as Msg, EventDiscussionModeration as Hist
from app.models.event_model import Event
from app.repository import event_repo
from app.repository.query_utils import build_pagination_meta
from app.services import event_discussion_moderation as moderation
from app.services import event_discussion_notifications as notify

logger = logging.getLogger(__name__)

ACTIVE_REGISTRATION = ("confirmed", "attended")
CLOSED_EVENT_STATUSES = ("cancelled", "completed", "archived")
STATUS_TEXT = {
    "pending_moderation": "Your message is being checked and will appear once it passes moderation.",
    "pending_review": "Your message is pending admin review.",
    "blocked": "Your message violates community guidelines and cannot be posted.",
    "deleted": "Your message was not approved.",
}


def _now() -> datetime:
    return datetime.utcnow()


def _token(request) -> str | None:
    from app.core.dependencies import extract_access_token

    return extract_access_token(request) if request is not None else None


# ---------------------------------------------------------------- who is calling

@dataclass
class Viewer:
    event: Event
    user_id: str
    name: str | None
    email: str | None
    role: str | None                 # participant | enterprise_admin | super_admin | None (locked)
    locked_reason: str | None = None
    token: str | None = None

    @property
    def can_read(self) -> bool:
        return self.role is not None

    @property
    def can_post(self) -> bool:
        return self.role in ("participant", "enterprise_admin") and not self.closed

    @property
    def can_moderate(self) -> bool:
        return self.role == "enterprise_admin"

    @property
    def closed(self) -> bool:
        return (self.event.status or "") in CLOSED_EVENT_STATUSES


def _event(db: Session, event_id: UUID) -> Event:
    event = event_repo.get_event_by_id(db, event_id)
    if event is None:
        raise problem(404, "This event is not available", "EVENT_NOT_FOUND")
    return event


def viewer_for(db: Session, event: Event, user: dict, token: str | None = None) -> Viewer:
    uid, name, email = str(user.get("id") or ""), user.get("name") or None, (user.get("email") or "").strip().lower() or None
    base = dict(event=event, user_id=uid, name=name, email=email, token=token)
    if event_repo.is_platform_super_admin(user):
        return Viewer(role="super_admin", **base)
    if user.get("role") == "admin":
        try:
            event_repo.assert_event_access(db, event, user, access_token=token)
            return Viewer(role="enterprise_admin", **base)
        except Exception:
            pass
    rows = db.query(EventRegistration.status).filter(EventRegistration.event_id == event.id, func.lower(EventRegistration.participant_email) == email).all() if email else []
    if any(r[0] in ACTIVE_REGISTRATION for r in rows):
        return Viewer(role="participant", **base)
    if user.get("role") == "admin":
        return Viewer(role=None, locked_reason="NOT_EVENT_ADMIN", **base)
    return Viewer(role=None, locked_reason="REGISTRATION_CANCELLED" if rows else "NOT_REGISTERED", **base)


def _touch_member(db: Session, viewer: Viewer) -> None:
    if viewer.role not in ("participant", "enterprise_admin") or not viewer.user_id:
        return
    row = db.query(EventDiscussionMember).filter_by(event_id=viewer.event.id, user_id=viewer.user_id).first()
    if row is None:
        db.add(EventDiscussionMember(event_id=viewer.event.id, user_id=viewer.user_id, name=viewer.name, email=viewer.email, role=viewer.role))
    else:
        row.last_seen_at, row.role = _now(), viewer.role
        row.name = viewer.name or row.name
    db.commit()


def _must_read(db: Session, viewer: Viewer) -> Viewer:
    if not viewer.can_read:
        raise problem(403, "The discussion opens once your registration for this event is confirmed" if viewer.locked_reason != "NOT_EVENT_ADMIN"
                      else "You do not manage this event", "DISCUSSION_LOCKED", reason=viewer.locked_reason)
    _touch_member(db, viewer)
    return viewer


def _must_post(db: Session, viewer: Viewer) -> Viewer:
    if viewer.role == "super_admin":
        raise problem(403, "Super Admin access to event discussions is read-only", "SUPER_ADMIN_READ_ONLY")
    _must_read(db, viewer)
    if viewer.closed:
        raise problem(409, "This discussion is closed. You can read it but not post", "DISCUSSION_CLOSED")
    return viewer


def _must_moderate(db: Session, viewer: Viewer) -> Viewer:
    if viewer.role == "super_admin":
        raise problem(403, "Super Admin access to event discussions is read-only", "SUPER_ADMIN_READ_ONLY")
    _must_read(db, viewer)
    if not viewer.can_moderate:
        raise problem(403, "Only the Enterprise Admin of this event can review flagged messages", "FORBIDDEN")
    return viewer


def access_for(db: Session, event_id: UUID, user: dict, token: str | None = None) -> dict:
    event = _event(db, event_id)
    viewer = viewer_for(db, event, user, token)
    if viewer.can_read:
        _touch_member(db, viewer)
    text = None
    if viewer.locked_reason == "NOT_REGISTERED":
        text = "Register for this event to join the discussion."
    elif viewer.locked_reason == "REGISTRATION_CANCELLED":
        text = "Your registration for this event is not active, so the discussion is closed to you."
    elif viewer.locked_reason == "NOT_EVENT_ADMIN":
        text = "You do not manage this event."
    elif viewer.can_read and viewer.closed:
        text = "This discussion is closed. You can read it but not post."
    return {"event_id": event.id, "event_name": event.title, "role": viewer.role, "can_read": viewer.can_read, "can_post": viewer.can_post,
            "can_moderate": viewer.can_moderate, "is_closed": viewer.can_read and viewer.closed, "locked_reason": viewer.locked_reason, "message": text}


# ---------------------------------------------------------------- history and responses

def _log(db: Session, msg: Msg, action: str, *, from_status: str | None, actor: Viewer | None = None, label: str | None = None,
         reason: str | None = None, detail: dict | None = None) -> None:
    db.add(Hist(event_id=msg.event_id, tenant_id=msg.tenant_id, message_id=msg.id, action=action, from_status=from_status, to_status=msg.status, label=label,
                reason=reason, actor_id=actor.user_id if actor else None, actor_name=actor.name if actor else None,
                actor_role=actor.role if actor else "system", detail=detail))


def _reply_counts(db: Session, ids: list) -> dict:
    if not ids:
        return {}
    rows = db.query(Msg.root_id, func.count(Msg.id)).filter(Msg.root_id.in_(ids), Msg.status == "published", Msg.parent_id.isnot(None)).group_by(Msg.root_id).all()
    return {r[0]: r[1] for r in rows}


def _out(msg: Msg, viewer: Viewer, counts: dict | None = None) -> dict:
    mine = msg.author_id == viewer.user_id
    return {
        "id": msg.id, "event_id": msg.event_id, "parent_id": msg.parent_id, "root_id": msg.root_id, "body": msg.body,
        "author": {"id": msg.author_id, "name": msg.author_name, "role": msg.author_role}, "is_announcement": bool(msg.is_announcement), "status": msg.status,
        "status_message": STATUS_TEXT.get(msg.status) if mine and msg.status != "published" else None, "is_mine": mine, "is_appeal": bool(msg.is_appeal),
        "reply_count": (counts or {}).get(msg.id, 0), "is_unread_reply": bool(msg.parent_id and msg.recipient_id == viewer.user_id and msg.recipient_read_at is None
                                                                             and msg.status == "published"),
        "created_at": msg.created_at, "published_at": msg.published_at,
    }


def _visible(query, viewer: Viewer):
    """Published messages for everyone; the caller's own messages in any state, so they can see "pending review" or "blocked"."""
    return query.filter(or_(Msg.status == "published", Msg.author_id == viewer.user_id))


def _unavailable():
    return problem(404, "This message is no longer available.", "MESSAGE_UNAVAILABLE")


def _load(db: Session, viewer: Viewer, message_id: UUID) -> Msg:
    msg = db.get(Msg, message_id)
    if msg is None or msg.event_id != viewer.event.id:
        raise _unavailable()
    if msg.status != "published" and msg.author_id != viewer.user_id:
        raise _unavailable()
    return msg


# ---------------------------------------------------------------- reading

def list_messages(db: Session, event_id: UUID, user: dict, token: str | None, *, parent_id: UUID | None, order: str, page: int, page_size: int) -> dict:
    viewer = _must_read(db, viewer_for(db, _event(db, event_id), user, token))
    query = _visible(db.query(Msg).filter(Msg.event_id == event_id), viewer)
    query = query.filter(Msg.parent_id == parent_id) if parent_id else query.filter(Msg.parent_id.is_(None))
    total = query.count()
    order_by = (Msg.is_announcement.desc(), Msg.created_at.desc()) if order == "newest" else (Msg.is_announcement.desc(), Msg.created_at.asc())
    rows = query.order_by(*order_by, Msg.id).offset((page - 1) * page_size).limit(page_size).all()
    counts = _reply_counts(db, [r.id for r in rows])
    return {"items": [_out(r, viewer, counts) for r in rows], "pagination": build_pagination_meta(total, page, page_size)}


def my_messages(db: Session, event_id: UUID, user: dict, token: str | None, *, status: str | None, page: int, page_size: int) -> dict:
    viewer = _must_read(db, viewer_for(db, _event(db, event_id), user, token))
    query = db.query(Msg).filter(Msg.event_id == event_id, Msg.author_id == viewer.user_id)
    if status:
        query = query.filter(Msg.status == status)
    total = query.count()
    rows = query.order_by(Msg.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": [_out(r, viewer) for r in rows], "pagination": build_pagination_meta(total, page, page_size)}


def get_message(db: Session, event_id: UUID, message_id: UUID, user: dict, token: str | None) -> dict:
    """One message with its replies. Opening it marks a reply to the caller as read and its notifications as read, which also ends the nudge."""
    viewer = _must_read(db, viewer_for(db, _event(db, event_id), user, token))
    msg = _load(db, viewer, message_id)
    root_id = msg.root_id or msg.id
    replies = _visible(db.query(Msg).filter(Msg.root_id == root_id, Msg.parent_id.isnot(None)), viewer).order_by(Msg.created_at, Msg.id).limit(200).all() if msg.parent_id is None else []
    for m in [msg, *replies]:
        if m.parent_id and m.recipient_id == viewer.user_id and m.recipient_read_at is None and m.status == "published":
            m.recipient_read_at = _now()
    notify.mark_read(db, viewer.user_id, msg.id)
    db.commit()
    out = _out(msg, viewer, _reply_counts(db, [msg.id]))
    out["is_unread_reply"] = False
    return {**out, "replies": [{**_out(r, viewer), "is_unread_reply": False} for r in replies]}


# ---------------------------------------------------------------- writing

def _clean_body(raw: str) -> str:
    text = "".join(ch for ch in (raw or "") if ch == "\n" or ch >= " ").strip()
    if not text:
        raise problem(422, "Write a message first", "VALIDATION_FAILED", errors=[{"field": "body", "code": "REQUIRED", "message": "Write a message first"}])
    if len(text) > settings.EVENT_DISCUSSION_MAX_CHARS:
        raise problem(422, f"A message can have at most {settings.EVENT_DISCUSSION_MAX_CHARS} characters", "MESSAGE_TOO_LONG",
                      errors=[{"field": "body", "code": "TOO_LONG", "message": f"At most {settings.EVENT_DISCUSSION_MAX_CHARS} characters"}])
    return text


def _moderate(db: Session, msg: Msg, actor: Viewer) -> list[str]:
    """Judge the message and move it to its next state. Returns what to notify after the commit: published and/or flagged."""
    msg.moderation_attempts = (msg.moderation_attempts or 0) + 1
    previous = msg.status
    try:
        verdict = moderation.classify(msg.body)
    except moderation.ModerationUnavailable as exc:
        msg.status = "pending_moderation"
        _log(db, msg, "ai_unavailable", from_status=previous, reason=str(exc)[:200])
        return []
    msg.moderation_label, msg.moderation_reason, msg.moderation_provider = verdict.label, verdict.reason, verdict.provider
    if verdict.label == "safe":
        msg.status, msg.published_at = "published", _now()
        _log(db, msg, "auto_published", from_status=previous, label="safe", detail={"provider": verdict.provider})
        return ["published"]
    if verdict.label == "flagged":
        msg.status = "pending_review"
        _log(db, msg, "flagged", from_status=previous, label="flagged", reason=verdict.reason, detail={"categories": verdict.categories, "provider": verdict.provider})
        return ["flagged"]
    msg.status = "blocked"
    _log(db, msg, "blocked", from_status=previous, label="extreme", reason=verdict.reason, detail={"categories": verdict.categories, "provider": verdict.provider})
    return []


def _after(msg: Msg, events: list[str], token: str | None) -> None:
    if "published" in events:
        notify.notify_published(msg.id)
    if "flagged" in events:
        notify.notify_flagged(msg.id, access_token=token)


def _result(msg: Msg, viewer: Viewer) -> dict:
    return {"message": _out(msg, viewer), "user_message": STATUS_TEXT.get(msg.status)}


def post_message(db: Session, event_id: UUID, user: dict, token: str | None, *, body: str, parent_id: UUID | None, is_announcement: bool) -> dict:
    viewer = _must_post(db, viewer_for(db, _event(db, event_id), user, token))
    text = _clean_body(body)
    if is_announcement and viewer.role != "enterprise_admin":
        raise problem(403, "Only the Enterprise Admin can post an announcement", "ANNOUNCEMENT_ADMIN_ONLY")
    parent = None
    if parent_id is not None:
        parent = db.get(Msg, parent_id)
        if parent is None or parent.event_id != event_id or parent.status != "published":
            raise problem(409, "The message you are replying to is no longer available", "PARENT_NOT_AVAILABLE")
        if is_announcement:
            raise problem(422, "An announcement cannot be a reply", "VALIDATION_FAILED")
    recent = db.query(Msg.id).filter(Msg.event_id == event_id, Msg.author_id == viewer.user_id, Msg.body == text, Msg.status != "deleted",
                                     Msg.created_at >= _now() - timedelta(seconds=30)).first()
    if recent is not None:
        raise problem(409, "You just sent this message", "DUPLICATE_MESSAGE")
    msg = Msg(event_id=event_id, tenant_id=_tenant(viewer.event), parent_id=parent.id if parent else None, root_id=(parent.root_id or parent.id) if parent else None,
              author_id=viewer.user_id, author_name=viewer.name, author_role=viewer.role, body=text, is_announcement=is_announcement,
              status="pending_moderation", recipient_id=parent.author_id if parent and parent.author_id != viewer.user_id else None)
    db.add(msg)
    db.flush()
    _log(db, msg, "submitted", from_status=None, actor=viewer)
    events = _moderate(db, msg, viewer)
    db.commit()
    db.refresh(msg)
    _after(msg, events, token)
    return _result(msg, viewer)


def _tenant(event: Event):
    value = event_repo.event_owner_tenant_id(event)
    return UUID(str(value)) if value else None


def edit_message(db: Session, event_id: UUID, message_id: UUID, user: dict, token: str | None, *, body: str) -> dict:
    """A blocked (or still unchecked) message can be edited and sent for moderation again."""
    viewer = _must_post(db, viewer_for(db, _event(db, event_id), user, token))
    msg = _load(db, viewer, message_id)
    if msg.author_id != viewer.user_id:
        raise problem(403, "You can only edit your own message", "NOT_AUTHOR")
    if msg.status not in ("blocked", "pending_moderation"):
        raise problem(409, "Only a blocked message can be edited and sent again", "INVALID_STATUS")
    previous = msg.status
    msg.body, msg.is_appeal = _clean_body(body), False
    msg.status, msg.moderation_label, msg.moderation_reason = "pending_moderation", None, None
    _log(db, msg, "edited_resubmitted", from_status=previous, actor=viewer)
    events = _moderate(db, msg, viewer)
    db.commit()
    db.refresh(msg)
    _after(msg, events, token)
    return _result(msg, viewer)


def appeal_message(db: Session, event_id: UUID, message_id: UUID, user: dict, token: str | None, *, note: str | None) -> dict:
    """Ask an Enterprise Admin to look at a message the AI blocked. One appeal per message."""
    viewer = _must_post(db, viewer_for(db, _event(db, event_id), user, token))
    msg = _load(db, viewer, message_id)
    if msg.author_id != viewer.user_id:
        raise problem(403, "You can only appeal your own message", "NOT_AUTHOR")
    if msg.status != "blocked":
        raise problem(409, "Only a blocked message can be appealed", "INVALID_STATUS")
    already = db.query(Hist.id).filter(Hist.message_id == msg.id, Hist.action == "appealed").first()
    if already is not None:
        raise problem(409, "This message was already appealed", "ALREADY_APPEALED")
    msg.status, msg.is_appeal = "pending_review", True
    _log(db, msg, "appealed", from_status="blocked", actor=viewer, reason=(note or "").strip()[:500] or None)
    db.commit()
    db.refresh(msg)
    _after(msg, ["flagged"], token)
    return _result(msg, viewer)


def decide(db: Session, event_id: UUID, message_id: UUID, user: dict, token: str | None, *, action: str, note: str | None) -> dict:
    viewer = _must_moderate(db, viewer_for(db, _event(db, event_id), user, token))
    msg = db.get(Msg, message_id)
    if msg is None or msg.event_id != event_id:
        raise _unavailable()
    if msg.status != "pending_review":
        raise problem(409, "Only a message that is waiting for review can be decided", "INVALID_STATUS")
    if action == "approve" and msg.author_id == viewer.user_id:
        raise problem(403, "You cannot approve your own message. Another Enterprise Admin of the event must", "SELF_REVIEW")
    previous = msg.status
    msg.decided_by, msg.decided_by_name, msg.decided_at, msg.decision_note = viewer.user_id, viewer.name, _now(), (note or "").strip()[:500] or None
    if action == "approve":
        msg.status, msg.published_at = "published", _now()
    else:
        msg.status = "deleted"
    _log(db, msg, "approved" if action == "approve" else "deleted", from_status=previous, actor=viewer, reason=msg.decision_note, label=msg.moderation_label)
    db.commit()
    db.refresh(msg)
    notify.notify_decision(msg.id, "approved" if action == "approve" else "deleted")
    if action == "approve":
        notify.notify_published(msg.id)
    return _result(msg, viewer) | {"user_message": None}


# ---------------------------------------------------------------- moderation lists and history

def _event_names(db: Session, ids) -> dict:
    ids = list(set(ids))
    return {e.id: e.title for e in db.query(Event.id, Event.title).filter(Event.id.in_(ids)).all()} if ids else {}


def _item(m: Msg, names: dict) -> dict:
    return {"message_id": m.id, "event_id": m.event_id, "event_name": names.get(m.event_id), "sender_id": m.author_id, "sender_name": m.author_name,
            "sender_role": m.author_role, "body": m.body, "status": m.status, "is_appeal": bool(m.is_appeal), "flagging_reason": m.moderation_reason,
            "submitted_at": m.created_at, "decided_by_name": m.decided_by_name, "decided_at": m.decided_at, "decision_note": m.decision_note, "parent_id": m.parent_id}


def _page(db: Session, query, page: int, page_size: int, pending_query) -> dict:
    total = query.count()
    rows = query.order_by(Msg.created_at.desc(), Msg.id).offset((page - 1) * page_size).limit(page_size).all()
    names = _event_names(db, [r.event_id for r in rows])
    return {"items": [_item(r, names) for r in rows], "pagination": build_pagination_meta(total, page, page_size), "pending_count": pending_query.count()}


def event_queue(db: Session, event_id: UUID, user: dict, token: str | None, *, status: str, page: int, page_size: int) -> dict:
    """The flagged messages of one event. Enterprise Admin and (read only) Super Admin."""
    viewer = _must_read(db, viewer_for(db, _event(db, event_id), user, token))
    if viewer.role not in ("enterprise_admin", "super_admin"):
        raise problem(403, "Only the Enterprise Admin of this event can see the review list", "FORBIDDEN")
    base = db.query(Msg).filter(Msg.event_id == event_id)
    return _page(db, base.filter(Msg.status == status), page, page_size, base.filter(Msg.status == "pending_review"))


def tenant_queue(db: Session, user: dict, token: str | None, *, event_id: UUID | None, tenant_id: UUID | None, status: str, page: int, page_size: int) -> dict:
    """Flagged messages across the events the caller manages (Enterprise Admin), or across every enterprise (Super Admin, read only)."""
    if event_repo.is_platform_super_admin(user):
        base = db.query(Msg)
        if tenant_id:
            base = base.filter(Msg.tenant_id == tenant_id)
    elif user.get("role") == "admin":
        tenant = event_repo.resolve_caller_tenant_id(db, user, access_token=token)
        if not tenant:
            raise problem(403, "Your business could not be determined", "FORBIDDEN")
        base = db.query(Msg).filter(Msg.tenant_id == UUID(str(tenant)))
    else:
        raise problem(403, "Only an Enterprise Admin or a Super Admin can see this", "FORBIDDEN")
    if event_id:
        base = base.filter(Msg.event_id == event_id)
    return _page(db, base.filter(Msg.status == status), page, page_size, base.filter(Msg.status == "pending_review"))


def history(db: Session, event_id: UUID, user: dict, token: str | None, *, action: str | None, message_id: UUID | None, page: int, page_size: int) -> dict:
    viewer = _must_read(db, viewer_for(db, _event(db, event_id), user, token))
    if viewer.role not in ("enterprise_admin", "super_admin"):
        raise problem(403, "Only the Enterprise Admin of this event can see the moderation history", "FORBIDDEN")
    query = db.query(Hist).filter(Hist.event_id == event_id)
    if action:
        query = query.filter(Hist.action == action)
    if message_id:
        query = query.filter(Hist.message_id == message_id)
    total = query.count()
    rows = query.order_by(Hist.created_at.desc(), Hist.id).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": rows, "pagination": build_pagination_meta(total, page, page_size)}


def activity(db: Session, user: dict, token: str | None, *, tenant_id: UUID | None, page: int, page_size: int) -> dict:
    """Discussion activity per event: counts by status. Enterprise Admin: their events. Super Admin: every enterprise (read only)."""
    query = db.query(Msg.event_id, Msg.tenant_id, Msg.status, func.count(Msg.id), func.max(Msg.created_at))
    if event_repo.is_platform_super_admin(user):
        if tenant_id:
            query = query.filter(Msg.tenant_id == tenant_id)
    elif user.get("role") == "admin":
        tenant = event_repo.resolve_caller_tenant_id(db, user, access_token=token)
        if not tenant:
            raise problem(403, "Your business could not be determined", "FORBIDDEN")
        query = query.filter(Msg.tenant_id == UUID(str(tenant)))
    else:
        raise problem(403, "Only an Enterprise Admin or a Super Admin can see this", "FORBIDDEN")
    events: dict = {}
    for event_id, tenant, status, count, last in query.group_by(Msg.event_id, Msg.tenant_id, Msg.status).all():
        row = events.setdefault(event_id, {"event_id": event_id, "tenant_id": tenant, "published": 0, "pending_review": 0, "blocked": 0, "deleted": 0,
                                           "pending_moderation": 0, "last_activity_at": None})
        row[status] = count
        row["last_activity_at"] = max(filter(None, [row["last_activity_at"], last]))
    ordered = sorted(events.values(), key=lambda r: r["last_activity_at"] or datetime.min, reverse=True)
    total = len(ordered)
    window = ordered[(page - 1) * page_size: page * page_size]
    names = _event_names(db, [r["event_id"] for r in window])
    return {"items": [{**r, "event_name": names.get(r["event_id"])} for r in window], "pagination": build_pagination_meta(total, page, page_size)}
