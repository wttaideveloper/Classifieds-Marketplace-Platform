import logging
from uuid import UUID

from fastapi import HTTPException

from app.core.config import settings
from app.db.database import SessionLocal
from app.realtime.auth import authenticate_token, extract_token_from_environ, get_dev_user
from app.realtime.emitters import (
    emit_conversation_updated,
    emit_message_read,
    emit_new_message,
    emit_typing,
    emit_user_offline,
    emit_user_online,
    notify_participants_new_message,
)
from app.realtime.rooms import conversation_room, serialize, user_room
from app.realtime.server import sio
from app.schemas.chat_schema import MessageCreate
from app.services.chat_service import update_presence_service
from app.services.socket_io_service import (
    process_mark_read,
    process_send_message,
    process_typing,
    validate_join_room,
    validate_leave_room,
)

logger = logging.getLogger(__name__)


async def _get_session_user(sid) -> dict | None:
    session = await sio.get_session(sid)
    return session.get("user") if session else None


async def _emit_error(sid, event: str, detail: str):
    await sio.emit("error", {"event": event, "detail": detail}, to=sid)


def _run_with_db(handler):
    async def wrapper(sid, data=None):
        if data is None:
            data = {}
        db = SessionLocal()
        try:
            user = await _get_session_user(sid)
            if not user:
                await _emit_error(sid, handler.__name__, "Not authenticated")
                return
            return await handler(sid, data, db, user)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            await _emit_error(sid, handler.__name__, detail)
        except Exception:
            logger.exception("Socket event %s failed for sid %s", handler.__name__, sid)
            await _emit_error(sid, handler.__name__, "Internal server error")
        finally:
            db.close()

    wrapper.__name__ = handler.__name__
    return wrapper


@sio.event
async def connect(sid, environ, auth):
    token = extract_token_from_environ(environ, auth)
    user = authenticate_token(token)
    if not user:
        if settings.is_production:
            return False
        user = get_dev_user()

    await sio.save_session(sid, {"user": user})
    await sio.enter_room(sid, user_room(user["id"]))

    db = SessionLocal()
    try:
        update_presence_service(db, user, "online")
    finally:
        db.close()

    await emit_user_online(user["id"], skip_sid=sid)
    return True


@sio.event
async def disconnect(sid):
    user = await _get_session_user(sid)
    if not user:
        return

    db = SessionLocal()
    try:
        update_presence_service(db, user, "offline")
    finally:
        db.close()

    await emit_user_offline(user["id"])


@sio.on("join_room")
@_run_with_db
async def join_room(sid, data, db, user):
    conversation_id = data.get("conversation_id")
    if not conversation_id:
        await _emit_error(sid, "join_room", "conversation_id is required")
        return

    conv_id = UUID(str(conversation_id))
    result = validate_join_room(db, user, conv_id)
    await sio.enter_room(sid, conversation_room(conv_id))
    await sio.emit("joined_room", serialize(result), to=sid)


@sio.on("leave_room")
@_run_with_db
async def leave_room(sid, data, db, user):
    conversation_id = data.get("conversation_id")
    if not conversation_id:
        await _emit_error(sid, "leave_room", "conversation_id is required")
        return

    conv_id = UUID(str(conversation_id))
    result = validate_leave_room(db, user, conv_id)
    await sio.leave_room(sid, conversation_room(conv_id))
    await sio.emit("left_room", serialize(result), to=sid)


@sio.on("send_message")
@_run_with_db
async def send_message(sid, data, db, user):
    conversation_id = data.get("conversation_id")
    if not conversation_id:
        await _emit_error(sid, "send_message", "conversation_id is required")
        return

    conv_id = UUID(str(conversation_id))
    message_data, conversation_data = process_send_message(
        db,
        user,
        MessageCreate(
            conversation_id=conv_id,
            content=data.get("content"),
            message_type=data.get("message_type", "text"),
            attachment_id=UUID(str(data["attachment_id"])) if data.get("attachment_id") else None,
        ),
    )

    await emit_new_message(conv_id, message_data)
    await emit_conversation_updated(conv_id, conversation_data)
    await notify_participants_new_message(
        db,
        conv_id,
        user["id"],
        message_data,
        conversation_data.get("last_message_preview") or "",
    )
    return message_data


@sio.on("typing_start")
@_run_with_db
async def typing_start(sid, data, db, user):
    conversation_id = data.get("conversation_id")
    if not conversation_id:
        await _emit_error(sid, "typing_start", "conversation_id is required")
        return

    conv_id = UUID(str(conversation_id))
    process_typing(db, user, conv_id, True)
    await emit_typing(conv_id, user["id"], True, skip_sid=sid)


@sio.on("typing_stop")
@_run_with_db
async def typing_stop(sid, data, db, user):
    conversation_id = data.get("conversation_id")
    if not conversation_id:
        await _emit_error(sid, "typing_stop", "conversation_id is required")
        return

    conv_id = UUID(str(conversation_id))
    process_typing(db, user, conv_id, False)
    await emit_typing(conv_id, user["id"], False, skip_sid=sid)


@sio.on("mark_read")
@_run_with_db
async def mark_read(sid, data, db, user):
    message_id = data.get("message_id")
    conversation_id = data.get("conversation_id")

    read_data, conv_id = process_mark_read(
        db,
        user,
        message_id=UUID(str(message_id)) if message_id else None,
        conversation_id=UUID(str(conversation_id)) if conversation_id else None,
    )
    await emit_message_read(conv_id, read_data, skip_sid=sid)
    return read_data
