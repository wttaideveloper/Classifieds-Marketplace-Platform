"""Notifications and realtime events for group chat.

Privacy rules that shape this module (docs/group-chat-api.md):

* A message notification in a PROGRAM group (which includes training and event groups) shows the group name, the sender's display name (a
  patient by display name, never a full name) and a preview of what was sent (chat_notification_content.py). In a CARE group, or when
  GROUP_NOTIFICATION_PREVIEW_ENABLED is off, it shows only "New message": no group name, no sender, no text, because a care team's messages are
  clinical and a preview would put them on a lock screen (GROUP_CARE_NOTIFICATION_PREVIEW_ENABLED turns it on for care groups too). The app opens the
  group from `metadata.conversation_id`. Email and SMS are not used for group messages at all, because they would carry text outside the app.
* An @-tag notifies the tagged person directly (still without the message text) and, in a care group, even when they
  muted the group.
* Everything here is best effort and runs after the change is saved: a problem sending never fails the API call.
"""
from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.chat_model import Conversation, ConversationParticipant, Message
from app.services.group_chat_common import member_is_muted_now

logger = logging.getLogger(__name__)

NEW_MESSAGE_TITLE = "New message"


# ---------------------------------------------------------------- the platform feed (and push) for one-off events

def notify(
    db: Session, *, category: str, user_ids, title: str, message: str, metadata: dict, tenant_id=None,
    channels=("in_app", "push"),
) -> None:
    """One notification on the existing feed and Socket.IO `notification` event. Never raises."""
    ids = [uid for uid in dict.fromkeys(user_ids) if uid]
    if not ids:
        return
    try:
        from app.services import notification_service

        notification_service.create_automatic_notification(
            db, title=title, message=message, category=category, user_ids=ids, tenant_id=tenant_id,
            metadata={**{k: v for k, v in metadata.items() if v is not None}, "category": category},
            channels=list(channels),
        )
    except Exception:
        logger.exception("group notification %s failed", category)
        try:
            db.rollback()
        except Exception:
            pass


def notify_invitation(db: Session, conversation: Conversation, invitees: list[UUID], invited_by_name: str | None) -> None:
    title = "You have been invited to a group"
    message = "Open the invitation to see what the group is for and who is in it. You see nothing until you accept."
    metadata = {"conversation_id": str(conversation.id), "group_kind": conversation.group_kind}
    if conversation.context_type == "event" and conversation.context_id:   # an event group says which event, so the app can open it
        from app.services.group_chat_event import context_names

        event_title = context_names(db, [conversation]).get(conversation.id)
        title = "You have been invited to an event group"
        message = (f"You are invited to the group for the event \"{event_title}\". " if event_title else "You are invited to an event group. ") + (
            "Open the invitation to see who is in it. You see nothing until you accept.")
        metadata.update({"context_type": "event", "context_id": str(conversation.context_id)})
    notify(db, category="group_invitation", user_ids=invitees, title=title, message=message, metadata=metadata, tenant_id=conversation.tenant_id)


def notify_removed(db: Session, conversation: Conversation, user_id: UUID) -> None:
    notify(
        db, category="group_removed", user_ids=[user_id], title="You were removed from a group",
        message="You no longer have access to this group.",
        metadata={"conversation_id": str(conversation.id)}, tenant_id=conversation.tenant_id,
    )


def notify_role_changed(db: Session, conversation: Conversation, user_id: UUID, role: str) -> None:
    notify(
        db, category="group_role_changed", user_ids=[user_id], title="Your role in a group changed",
        message=f"You are now {'an admin' if role == 'admin' else 'a member' if role == 'member' else 'the owner'} of the group.",
        metadata={"conversation_id": str(conversation.id), "role": role}, tenant_id=conversation.tenant_id,
    )


def notify_closed(db: Session, conversation: Conversation, members: list[UUID], reopened: bool = False) -> None:
    notify(
        db, category="group_reopened" if reopened else "group_closed", user_ids=members,
        title="A group was reopened" if reopened else "A group was closed",
        message="You can send messages again." if reopened else "This group is now read-only.",
        metadata={"conversation_id": str(conversation.id)}, tenant_id=conversation.tenant_id, channels=("in_app",),
    )


def notify_report(db: Session, conversation: Conversation, recipients: list[UUID], report_id: UUID, about_message: bool) -> None:
    if not recipients:
        logger.warning(
            "group report %s was saved but nobody could be notified: no clinic or platform admin was found "
            "(check the identity service settings). It is still listed under GET /groups/reports.", report_id,
        )
    notify(
        db, category="group_report_filed", user_ids=recipients, title="A group report needs review",
        message="A message was reported." if about_message else "A group was reported.",
        metadata={"conversation_id": str(conversation.id), "report_id": str(report_id), "entity_type": "group"},
        tenant_id=conversation.tenant_id, channels=("in_app",),
    )


# ---------------------------------------------------------------- a new message

def previews_enabled(conversation: Conversation) -> bool:
    from app.core.config import settings

    if not settings.GROUP_NOTIFICATION_PREVIEW_ENABLED:
        return False
    return conversation.group_kind != "care" or settings.GROUP_CARE_NOTIFICATION_PREVIEW_ENABLED


def sender_label(db: Session, conversation: Conversation, sender: ConversationParticipant | None, sender_id) -> str | None:
    """The sender as the group shows them: a patient by display name ("Priya S."), a provider by full name. Never an e-mail address."""
    from app.services import group_chat_common as common

    full = common.resolve_names(db, conversation, [sender_id]).get(str(sender_id))
    if sender is not None and sender.member_kind == "patient":
        return sender.display_name or common.default_display_name(full) or "A member"
    return full or (sender.display_name if sender is not None else None) or None


def content_for(db: Session, conversation: Conversation, message, sender_id, sender: ConversationParticipant | None = None):
    """What a new group message says (title, preview, data), or None when this group shows only "New message"."""
    if not previews_enabled(conversation):
        return None
    from app.repository import chat_repo
    from app.services import chat_notification_content as notice

    attachment = chat_repo.get_attachment_by_id(db, message.attachment_id) if getattr(message, "attachment_id", None) else None
    return notice.build(
        notice.GROUP, title=conversation.subject or notice.GENERIC_TITLE, conversation_id=conversation.id, message=message,
        attachment=attachment, sender_name=sender_label(db, conversation, sender, sender_id),
    )


def notify_new_message(
    db: Session, conversation: Conversation, message: Message, sender_id: UUID, members: list[ConversationParticipant],
) -> None:
    """Tell the other active members: group name, sender and preview in a program group; only "New message" in a care group."""
    try:
        from app.repository import chat_repo
        from app.services import chat_notification_service as cns

        sender = next((m for m in members if m.user_id == sender_id), None)
        content = content_for(db, conversation, message, sender_id, sender)
        title = content.title if content else NEW_MESSAGE_TITLE
        body = content.body if content else ""
        now = datetime.utcnow()
        mentioned = {str(u) for u in (message.mentions or [])}
        recipients: list[UUID] = []
        for member in members:
            if member.user_id == sender_id:
                continue
            tagged = message.mentions_all or str(member.user_id) in mentioned
            muted = member_is_muted_now(member, now)
            # A tag reaches a muted member only in a care group; a program group's mute is always respected.
            if muted and not (tagged and conversation.group_kind == "care"):
                continue
            recipients.append(member.user_id)
            prefs = chat_repo.get_notification_preferences(db, member.user_id)
            if prefs.in_app_enabled:
                chat_repo.create_notification(
                    db, user_id=member.user_id, notification_type="group_message", title=title, body=body,
                    data={"conversation_id": str(conversation.id), "message_id": str(message.id), "mentioned": tagged, **(content.data if content else {})},
                )
            if prefs.push_enabled:
                cns._dispatch_push_notification(db, member.user_id, title, body, conversation.id, message.id, kind="group_message", extra=content.data if content else None)

        if recipients:
            notify(
                db, category="group_message", user_ids=recipients, title=title, message=body,
                metadata={"conversation_id": str(conversation.id), "message_id": str(message.id), **(content.data if content else {})},
                tenant_id=conversation.tenant_id, channels=("in_app",),
            )
    except Exception:
        logger.exception("group message notification failed")
        try:
            db.rollback()
        except Exception:
            pass


# ---------------------------------------------------------------- realtime (Socket.IO)

def emit_group(conversation_id, event: str, payload: dict) -> None:
    """Send `event` to everyone currently in the group's room. Never raises."""
    try:
        from app.realtime.loop_bridge import run_coroutine
        from app.realtime.rooms import conversation_room, serialize
        from app.realtime.server import sio

        run_coroutine(sio.emit(event, serialize({"conversation_id": str(conversation_id), "conversation_type": "group", **payload}),
                               room=conversation_room(conversation_id)))
    except Exception:
        logger.exception("group realtime emit %s failed", event)


def emit_user(user_id, event: str, payload: dict) -> None:
    """Send `event` to one person's own room (they may not be in the group's room, e.g. just removed)."""
    try:
        from app.realtime.loop_bridge import run_coroutine
        from app.realtime.rooms import serialize, user_room
        from app.realtime.server import sio

        run_coroutine(sio.emit(event, serialize({**payload, "conversation_type": "group"}), room=user_room(user_id)))
    except Exception:
        logger.exception("group realtime emit %s failed", event)


def sync_rooms(user_id, conversation_id, *, join: bool) -> None:
    """Put a person's connected devices into the group's room (they accepted) or take them out (removed / left), so a
    removed member stops receiving messages straight away, even with the app open. With Redis configured the change is
    published so whichever process holds their connection applies it (app/realtime/room_sync.py)."""
    try:
        from app.realtime.loop_bridge import run_coroutine
        from app.realtime.room_sync import move

        run_coroutine(move(str(user_id), str(conversation_id), join))
    except Exception:
        logger.exception("could not sync socket rooms")


def emit_read(db: Session, conversation: Conversation, reader_id: UUID, read_at) -> None:
    """Tell the people who may see read receipts that `reader_id` has read the group up to `read_at`.

    Same visibility as the read-status endpoint: in a care group every active member; in a program group only providers
    (patients never learn who has read). The reader's own devices always get it, so another device can clear its unread count."""
    try:
        from app.services import group_chat_common as common

        boundary = (
            db.query(Message.id)
            .filter(Message.conversation_id == conversation.id, Message.message_type != "system", Message.is_deleted.is_(False), Message.created_at <= read_at)
            .order_by(Message.created_at.desc(), Message.id.desc()).first()
        )
        through = str(boundary[0]) if boundary else None
        for member in common.active_members(db, conversation.id):
            if member.user_id != reader_id and conversation.group_kind == "program" and member.member_kind != "provider":
                continue
            emit_user(member.user_id, "group_read", {"conversation_id": str(conversation.id), "user_id": str(reader_id), "read_at": read_at,
                                                  "read_through_message_id": through})
    except Exception:
        logger.exception("group read emit failed")
