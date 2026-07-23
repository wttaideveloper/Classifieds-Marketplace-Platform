from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.repository import chat_repo as chat_repo
from app.realtime.rooms import conversation_room, serialize
from app.schemas.chat_schema import MessageCreate
from app.services.chat_service import (
    mark_conversation_read_service,
    mark_message_read_service,
    send_message_service,
    update_typing_service,
)


def validate_join_room(db: Session, current_user: dict, conversation_id: UUID) -> dict:
    user_id = UUID(str(current_user["id"]))
    if not chat_repo.is_participant(db, conversation_id, user_id):
        raise HTTPException(status_code=403, detail="Not authorized to join this conversation")
    return {
        "conversation_id": str(conversation_id),
        "room": conversation_room(conversation_id),
        "authorized": True,
    }


def validate_leave_room(db: Session, current_user: dict, conversation_id: UUID) -> dict:
    user_id = UUID(str(current_user["id"]))
    if not chat_repo.is_participant(db, conversation_id, user_id):
        raise HTTPException(status_code=403, detail="Not authorized")
    return {
        "conversation_id": str(conversation_id),
        "room": conversation_room(conversation_id),
    }


def process_send_message(db: Session, current_user: dict, data: MessageCreate) -> tuple[dict, dict]:
    message = send_message_service(db, current_user, data)
    message_data = serialize(message)

    conversation = chat_repo.get_conversation_by_id(db, data.conversation_id)
    conversation_data = serialize({
        "id": conversation.id,
        "status": conversation.status,
        "last_message_at": conversation.last_message_at,
        "last_message_preview": conversation.last_message_preview,
        "updated_at": conversation.updated_at,
    })
    return message_data, conversation_data


def process_typing(
    db: Session,
    current_user: dict,
    conversation_id: UUID,
    is_typing: bool,
) -> dict:
    indicator = update_typing_service(db, current_user, conversation_id, is_typing)
    return serialize({
        "conversation_id": str(indicator.conversation_id),
        "user_id": str(indicator.user_id),
        "is_typing": indicator.is_typing,
        "updated_at": indicator.updated_at,
    })


def process_mark_read(
    db: Session,
    current_user: dict,
    *,
    message_id: UUID | None = None,
    conversation_id: UUID | None = None,
) -> tuple[dict, UUID]:
    if message_id:
        message = chat_repo.get_message_by_id(db, message_id)
        if not message:
            raise HTTPException(status_code=404, detail="Message not found")
        receipt = mark_message_read_service(db, current_user, message_id)
        return serialize(receipt.model_dump()), message.conversation_id

    if conversation_id:
        result = mark_conversation_read_service(db, current_user, conversation_id)
        return serialize(result), conversation_id

    raise HTTPException(status_code=400, detail="message_id or conversation_id is required")
