"""Event Discussion notifications, on the existing platform feed and Socket.IO ``notification`` event.

    event_discussion_reply             -> the author of the message replied to      someone replied to your message
    event_discussion_announcement      -> the event's known participants            an Enterprise Admin posted an announcement
    event_discussion_message_approved  -> the sender                                an Enterprise Admin approved the flagged message
    event_discussion_message_deleted   -> the sender                                an Enterprise Admin deleted it (never published)
    event_discussion_flagged           -> the event's Enterprise Admins             the AI flagged a message (or an appeal came in: metadata.is_appeal)
    event_discussion_pending_review    -> the event's Enterprise Admins             flagged messages are still waiting (a nudge)
    event_discussion_unread_reply      -> the author of the message replied to      a reply is still unread (a nudge)

Every one carries event_id, event_name, message_id, parent_id (when a reply), status, occurred_at and `deep_link`, the screen it opens for
that reader (the settings EVENT_DISCUSSION_LINK_*), so a tap goes to the exact message or review screen and not the Home page. The AI's
reason is only in what the Enterprise Admin gets, and is left out of push text. Sent after the change is committed, from a background
thread, never able to break the call that caused it; each delivery is claimed with a key so a retry cannot send it twice.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from uuid import UUID

from app.core.config import settings
from app.services import notification_idempotency

logger = logging.getLogger(__name__)


def _dispatch(fn, *args, **kwargs) -> None:
    def runner():
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.exception("event discussion notification failed")

    threading.Thread(target=runner, name="event-discussion-notification", daemon=True).start()


def link(template: str, event_id, message_id=None) -> str:
    return template.replace("{event_id}", str(event_id)).replace("{message_id}", str(message_id or ""))


def _deliver(db, *, recipients, category, title, message, metadata, dedupe_key, tenant_id=None):
    from app.services.notification_service import create_automatic_notification

    users = []
    for r in recipients or []:
        try:
            users.append(UUID(str(r)))
        except (ValueError, TypeError):
            continue
    if not users:
        logger.warning("%s NOT delivered: no recipients (%s)", category, dedupe_key)
        return None
    if not notification_idempotency.claim(db, dedupe_key):
        return None
    payload = {k: v for k, v in {**metadata, "category": category}.items() if v is not None}
    try:
        return create_automatic_notification(db, title=title, message=message, category=category, user_ids=users, tenant_id=None, metadata=payload,
                                             channels=["in_app", "push"])
    except Exception:
        logger.exception("%s delivery failed; releasing claim %s", category, dedupe_key)
        db.rollback()
        notification_idempotency.release(db, dedupe_key)
        return None


def _meta(event, message, status: str | None = None, **extra) -> dict:
    return {
        "entity_type": "event_discussion_message", "event_id": str(event.id), "event_name": getattr(event, "title", None) or getattr(event, "name", None),
        "message_id": str(message.id) if message else None, "parent_id": str(message.parent_id) if message and message.parent_id else None,
        "status": status or (message.status if message else None), "occurred_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z", **extra,
    }


def _admin_ids(db, event, access_token=None) -> list[str]:
    """Known Enterprise Admins of the event (they opened the discussion) plus the owning tenant's admins from the identity service."""
    from app.models.event_discussion_model import EventDiscussionMember

    ids = {m.user_id for m in db.query(EventDiscussionMember).filter_by(event_id=event.id, role="enterprise_admin").all()}
    try:
        from app.services.event_notification_service import resolve_enterprise_admin_user_ids

        found, _tenant = resolve_enterprise_admin_user_ids(db, event, access_token=access_token)
        ids |= {str(u) for u in found}
    except Exception:
        logger.debug("could not list the Enterprise Admins of event %s", event.id, exc_info=True)
    return sorted(ids)


def _load(db, message_id):
    from app.models.event_discussion_model import EventDiscussionMessage
    from app.models.event_model import Event

    message = db.get(EventDiscussionMessage, UUID(str(message_id)))
    event = db.get(Event, message.event_id) if message else None
    return message, event


# ---- a message was published: tell whoever it concerns -----------------------------------------------------------

def notify_published(message_id, *, access_token: str | None = None) -> None:
    """Call after a message became visible (auto-published or approved): a reply tells the person replied to, an announcement tells the participants."""
    _dispatch(_deliver_published, str(message_id))


def _deliver_published(message_id: str):
    from app.db.database import SessionLocal
    from app.models.event_discussion_model import EventDiscussionMember

    with SessionLocal() as db:
        message, event = _load(db, message_id)
        if message is None or event is None or message.status != "published":
            return None
        if message.is_announcement:
            members = [m.user_id for m in db.query(EventDiscussionMember).filter_by(event_id=event.id, role="participant").all() if m.user_id != message.author_id]
            return _deliver(
                db, recipients=members, category="event_discussion_announcement", title="New announcement",
                message="New announcement in your event discussion.", metadata=_meta(event, message, deep_link=link(settings.EVENT_DISCUSSION_LINK_PARTICIPANT, event.id, message.id),
                                                                                     author_name=message.author_name),
                dedupe_key=f"event_discussion_announcement:{message.id}",
            )
        if message.parent_id and message.recipient_id and message.recipient_id != message.author_id:
            admin = message.recipient_id in set(_admin_ids(db, event))
            template = settings.EVENT_DISCUSSION_LINK_ADMIN_MESSAGE if admin else settings.EVENT_DISCUSSION_LINK_PARTICIPANT
            text = "A participant replied to your discussion." if admin else "Someone replied to your discussion message."
            return _deliver(
                db, recipients=[message.recipient_id], category="event_discussion_reply", title="New reply", message=text,
                metadata=_meta(event, message, deep_link=link(template, event.id, message.id), author_name=message.author_name),
                dedupe_key=f"event_discussion_reply:{message.id}",
            )
    return None


def notify_flagged(message_id, *, access_token: str | None = None) -> None:
    _dispatch(_deliver_flagged, str(message_id), access_token)


def _deliver_flagged(message_id: str, access_token: str | None):
    from app.db.database import SessionLocal

    with SessionLocal() as db:
        message, event = _load(db, message_id)
        if message is None or event is None or message.status != "pending_review":
            return None
        admins = [a for a in _admin_ids(db, event, access_token) if a != message.author_id] or _admin_ids(db, event, access_token)
        text = "A discussion message appeal requires your review." if message.is_appeal else "A discussion message requires your review."
        return _deliver(
            db, recipients=admins, category="event_discussion_flagged", title="Discussion message to review", message=text,
            metadata=_meta(event, message, deep_link=link(settings.EVENT_DISCUSSION_LINK_ADMIN_FLAGGED, event.id, message.id), author_name=message.author_name,
                           is_appeal=bool(message.is_appeal), reason=message.moderation_reason),
            dedupe_key=f"event_discussion_flagged:{message.id}:{'appeal' if message.is_appeal else 'ai'}",
        )


def notify_decision(message_id, outcome: str) -> None:
    """Tell the sender what an Enterprise Admin decided: approved or deleted."""
    _dispatch(_deliver_decision, str(message_id), outcome)


def _deliver_decision(message_id: str, outcome: str):
    from app.db.database import SessionLocal

    with SessionLocal() as db:
        message, event = _load(db, message_id)
        if message is None or event is None:
            return None
        approved = outcome == "approved"
        category = "event_discussion_message_approved" if approved else "event_discussion_message_deleted"
        return _deliver(
            db, recipients=[message.author_id], category=category, title="Message approved" if approved else "Message not approved",
            message="Your discussion message has been approved." if approved else "Your discussion message was not approved.",
            metadata=_meta(event, message, deep_link=link(settings.EVENT_DISCUSSION_LINK_PARTICIPANT, event.id, message.id)),
            dedupe_key=f"{category}:{message.id}:{message.decided_at.isoformat() if message.decided_at else ''}",
        )


# ---- nudges ---------------------------------------------------------------------------------------------------------------

def nudge_unread_reply(db, message, event) -> bool:
    admin = message.recipient_id in set(_admin_ids(db, event))
    template = settings.EVENT_DISCUSSION_LINK_ADMIN_MESSAGE if admin else settings.EVENT_DISCUSSION_LINK_PARTICIPANT
    return bool(_deliver(
        db, recipients=[message.recipient_id], category="event_discussion_unread_reply", title="Unread reply",
        message="You have an unread reply in your event discussion.", metadata=_meta(event, message, deep_link=link(template, event.id, message.id), nudge=True),
        dedupe_key=f"event_discussion_unread_reply:{message.id}",
    ))


def nudge_pending_review(db, event, count: int, number: int) -> bool:
    plural = "message" if count == 1 else "messages"
    return bool(_deliver(
        db, recipients=_admin_ids(db, event), category="event_discussion_pending_review", title="Discussion messages waiting for review",
        message=f"You have {count} flagged discussion {plural} waiting for review.",
        metadata=_meta(event, None, status="pending_review", deep_link=link(settings.EVENT_DISCUSSION_LINK_ADMIN_QUEUE, event.id), pending_count=count, nudge=True),
        dedupe_key=f"event_discussion_pending_review:{event.id}:{number}",
    ))


def mark_read(db, user_id: str, message_id) -> int:
    """Opening a message marks the notifications about it as read."""
    from sqlalchemy import String, cast

    from app.models.notification_model import Notification, UserNotification

    try:
        uid = UUID(str(user_id))
    except (ValueError, TypeError):
        return 0
    ids = [r[0] for r in db.query(Notification.id).filter(Notification.category.like("event_discussion_%"),
                                                        cast(Notification.metadata_json, String).like(f"%{message_id}%")).all()]
    if not ids:
        return 0
    count = db.query(UserNotification).filter(UserNotification.user_id == uid, UserNotification.is_read.is_(False),
                                              UserNotification.notification_id.in_(ids)).update(
        {UserNotification.is_read: True, UserNotification.read_at: datetime.utcnow()}, synchronize_session=False)
    return count
