import mimetypes
import os
import uuid
from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.chat_model import ChatAttachment, Conversation, ConversationParticipant, Message
from app.repository import chat_repo as chat_repo

ALLOWED_MIME_TYPES: dict[str, set[str]] = {
    "image": {"image/jpeg", "image/png", "image/gif", "image/webp"},
    "document": {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
    },
    "audio": {"audio/mpeg", "audio/wav", "audio/ogg", "audio/mp4", "audio/webm"},
    "video": {"video/mp4", "video/webm", "video/quicktime"},
}

MAX_SIZE_BYTES: dict[str, int] = {
    "image": settings.MAX_IMAGE_SIZE_MB * 1024 * 1024,
    "document": settings.MAX_DOCUMENT_SIZE_MB * 1024 * 1024,
    "audio": settings.MAX_AUDIO_SIZE_MB * 1024 * 1024,
    "video": settings.MAX_VIDEO_SIZE_MB * 1024 * 1024,
}


def _parse_user_id(current_user: dict) -> UUID:
    return UUID(str(current_user["id"]))


def _ensure_participant(db: Session, conversation_id: UUID, user_id: UUID) -> None:
    if not chat_repo.is_participant(db, conversation_id, user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a participant in this conversation",
        )


def _detect_attachment_type(mime_type: str) -> str | None:
    for att_type, mimes in ALLOWED_MIME_TYPES.items():
        if mime_type in mimes:
            return att_type
    return None


def upload_attachment_service(
    db: Session,
    current_user: dict,
    *,
    conversation_id: UUID,
    file: UploadFile,
    attachment_type: str | None = None,
):
    user_id = _parse_user_id(current_user)
    conversation = chat_repo.get_conversation_by_id(db, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    _ensure_participant(db, conversation_id, user_id)

    if conversation.status != "open":
        raise HTTPException(status_code=400, detail="Cannot upload to a closed or archived conversation")
    if conversation.is_read_only:
        raise HTTPException(status_code=400, detail="Conversation is read-only")

    content = file.file.read()
    file_size = len(content)
    mime_type = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"

    detected_type = attachment_type or _detect_attachment_type(mime_type)
    if not detected_type:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {mime_type}")

    max_size = MAX_SIZE_BYTES.get(detected_type, MAX_SIZE_BYTES["document"])
    if file_size > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds maximum size of {max_size // (1024 * 1024)}MB for {detected_type}",
        )

    upload_dir = Path(settings.UPLOAD_DIR) / str(conversation_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    safe_name = file.filename or "upload"
    stored_name = f"{uuid.uuid4()}_{safe_name}"
    file_path = upload_dir / stored_name

    with open(file_path, "wb") as f:
        f.write(content)

    attachment = ChatAttachment(
        conversation_id=conversation_id,
        uploaded_by=user_id,
        file_name=safe_name,
        file_path=str(file_path),
        mime_type=mime_type,
        file_size=file_size,
        attachment_type=detected_type,
    )
    attachment = chat_repo.create_attachment(db, attachment)

    return {
        "id": attachment.id,
        "conversation_id": attachment.conversation_id,
        "message_id": attachment.message_id,
        "uploaded_by": attachment.uploaded_by,
        "file_name": attachment.file_name,
        "mime_type": attachment.mime_type,
        "file_size": attachment.file_size,
        "attachment_type": attachment.attachment_type,
        "download_url": f"/api/v1/attachments/{attachment.id}",
        "created_at": attachment.created_at,
    }


def get_attachment_service(db: Session, current_user: dict, attachment_id: UUID):
    user_id = _parse_user_id(current_user)
    attachment = chat_repo.get_attachment_by_id(db, attachment_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    _ensure_participant(db, attachment.conversation_id, user_id)

    return {
        "id": attachment.id,
        "conversation_id": attachment.conversation_id,
        "message_id": attachment.message_id,
        "uploaded_by": attachment.uploaded_by,
        "file_name": attachment.file_name,
        "mime_type": attachment.mime_type,
        "file_size": attachment.file_size,
        "attachment_type": attachment.attachment_type,
        "download_url": f"/api/v1/attachments/{attachment.id}",
        "created_at": attachment.created_at,
    }


def download_attachment_service(db: Session, current_user: dict, attachment_id: UUID):
    user_id = _parse_user_id(current_user)
    attachment = chat_repo.get_attachment_by_id(db, attachment_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    _ensure_participant(db, attachment.conversation_id, user_id)

    if not os.path.exists(attachment.file_path):
        raise HTTPException(status_code=404, detail="Attachment file not found on disk")

    return attachment


def delete_attachment_service(db: Session, current_user: dict, attachment_id: UUID):
    user_id = _parse_user_id(current_user)
    attachment = chat_repo.get_attachment_by_id(db, attachment_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    if attachment.uploaded_by != user_id and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to delete this attachment")

    chat_repo.soft_delete_attachment(db, attachment)
    return {
        "id": attachment.id,
        "is_deleted": True,
        "message": "Attachment deleted successfully",
    }
