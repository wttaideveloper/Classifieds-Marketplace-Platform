from uuid import UUID

from app.realtime.rooms import conversation_room, serialize, user_room
from app.realtime.server import sio


async def emit_new_message(conversation_id, message: dict, *, skip_sid: str | None = None, extra: dict | None = None):
    payload = serialize(message)
    await sio.emit(
        "new_message",
        {"conversation_id": str(conversation_id), **(extra or {}), "message": payload},
        room=conversation_room(conversation_id),
        skip_sid=skip_sid,
    )


async def emit_message_read(conversation_id, read_payload: dict, *, skip_sid: str | None = None):
    await sio.emit(
        "message_read",
        serialize({"conversation_id": str(conversation_id), **read_payload}),
        room=conversation_room(conversation_id),
        skip_sid=skip_sid,
    )


async def emit_typing(conversation_id, user_id, is_typing: bool, *, skip_sid: str | None = None, extra: dict | None = None):
    await sio.emit(
        "typing",
        serialize({
            "conversation_id": str(conversation_id),
            **(extra or {}),
            "user_id": str(user_id),
            "is_typing": is_typing,
        }),
        room=conversation_room(conversation_id),
        skip_sid=skip_sid,
    )


async def emit_message_updated(conversation_id, message: dict, *, skip_sid: str | None = None):
    await sio.emit(
        "message_updated",
        serialize({"conversation_id": str(conversation_id), "message": message}),
        room=conversation_room(conversation_id),
        skip_sid=skip_sid,
    )


async def emit_message_deleted(conversation_id, message_id, *, skip_sid: str | None = None):
    await sio.emit(
        "message_deleted",
        serialize({"conversation_id": str(conversation_id), "message_id": str(message_id)}),
        room=conversation_room(conversation_id),
        skip_sid=skip_sid,
    )


async def emit_conversation_updated(conversation_id, conversation: dict, *, extra: dict | None = None):
    await sio.emit(
        "conversation_updated",
        serialize({"conversation_id": str(conversation_id), **(extra or {}), "conversation": conversation}),
        room=conversation_room(conversation_id),
    )


async def emit_user_online(user_id, *, skip_sid: str | None = None):
    await sio.emit(
        "user_online",
        serialize({"user_id": str(user_id), "status": "online"}),
        skip_sid=skip_sid,
    )


async def emit_user_offline(user_id):
    await sio.emit(
        "user_offline",
        serialize({"user_id": str(user_id), "status": "offline"}),
    )


async def emit_notification(user_id, notification: dict):
    await sio.emit(
        "notification",
        serialize(notification),
        room=user_room(user_id),
    )


def _message_view(message: dict):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=message.get("id"), content=message.get("content"), message_type=message.get("message_type") or "text",
        attachment_id=message.get("attachment_id"),
    )


def _group_content(db, conversation, message: dict, sender_id):
    """The group message's title / preview / data for the socket `notification` event (None: this group shows only "New message")."""
    try:
        from app.services import group_chat_common as common
        from app.services.group_chat_notify import content_for

        view = _message_view(message)
        sender = common.get_member(db, conversation.id, UUID(str(sender_id)))
        return content_for(db, conversation, view, UUID(str(sender_id)), sender)
    except Exception:
        return None


def _one_to_one_content(db, conversation, message: dict, sender_id):
    try:
        from app.repository import chat_repo
        from app.services import chat_notification_content as notice

        view = _message_view(message)
        attachment = chat_repo.get_attachment_by_id(db, view.attachment_id) if view.attachment_id else None
        name = notice.sender_name_for(db, conversation, UUID(str(sender_id)))
        return notice.build(notice.ONE_TO_ONE, title=name or conversation.subject or notice.GENERIC_TITLE, conversation_id=conversation.id,
                            message=view, attachment=attachment, sender_name=name)
    except Exception:
        return None


async def notify_participants_new_message(
    db,
    conversation_id: UUID,
    sender_id: str,
    message: dict,
    preview: str,
):
    from app.repository import chat_repo as chat_repo

    conversation = chat_repo.get_conversation_by_id(
        db, conversation_id, with_participants=True
    )
    if not conversation:
        return

    is_group = conversation.conversation_type == "group"
    group_content, group_content_tried = None, False
    one_to_one = None if is_group else _one_to_one_content(db, conversation, message, sender_id)
    for participant in conversation.participants:
        participant_id = str(participant.user_id)
        if participant_id == sender_id or participant.membership_status != "active":
            continue  # only active members are ever told about a message

        if is_group:
            # A group notification carries no group name, no sender and no text (lock-screen privacy). Members who
            # muted the group are skipped, except a tagged member of a care group (see group_chat_notify).
            from app.services.group_chat_common import member_is_muted_now

            tagged = bool(message.get("mentions_all")) or participant_id in [str(x) for x in (message.get("mentions") or [])]
            if member_is_muted_now(participant) and not (tagged and conversation.group_kind == "care"):
                continue
            if group_content is None and not group_content_tried:
                group_content, group_content_tried = _group_content(db, conversation, message, sender_id), True
            await emit_notification(participant_id, {
                "type": "group_message",
                "conversation_id": str(conversation_id),
                "conversation_type": "group",
                "title": group_content.title if group_content else "New message",
                "body": group_content.body if group_content else "",
                **(group_content.data if group_content else {}),
            })
            continue

        await emit_notification(participant_id, {
            "type": "new_message",
            "conversation_id": str(conversation_id),
            "title": one_to_one.title if one_to_one else (conversation.subject or "New message"),
            "body": one_to_one.body if one_to_one and one_to_one.body else preview,
            "message": message,
            **(one_to_one.data if one_to_one else {}),
        })
