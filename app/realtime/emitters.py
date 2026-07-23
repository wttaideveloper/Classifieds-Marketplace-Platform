from uuid import UUID

from app.realtime.rooms import conversation_room, serialize, user_room
from app.realtime.server import sio


async def emit_new_message(conversation_id, message: dict, *, skip_sid: str | None = None):
    payload = serialize(message)
    await sio.emit(
        "new_message",
        {"conversation_id": str(conversation_id), "message": payload},
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


async def emit_typing(conversation_id, user_id, is_typing: bool, *, skip_sid: str | None = None):
    await sio.emit(
        "typing",
        serialize({
            "conversation_id": str(conversation_id),
            "user_id": str(user_id),
            "is_typing": is_typing,
        }),
        room=conversation_room(conversation_id),
        skip_sid=skip_sid,
    )


async def emit_conversation_updated(conversation_id, conversation: dict):
    await sio.emit(
        "conversation_updated",
        serialize({"conversation_id": str(conversation_id), "conversation": conversation}),
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

    for participant in conversation.participants:
        participant_id = str(participant.user_id)
        if participant_id == sender_id:
            continue

        await emit_notification(participant_id, {
            "type": "new_message",
            "conversation_id": str(conversation_id),
            "title": conversation.subject or "New message",
            "body": preview,
            "message": message,
        })
