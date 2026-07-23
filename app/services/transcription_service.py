from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.chat_model import ChatAttachment
from app.repository import chat_repo as chat_repo
from app.schemas.chat_schema import TranscribeResponse
from app.services.attachment_storage import (
    raise_attachment_file_unavailable,
    resolve_attachment_file_path,
)
from app.services.speech_to_text_service import is_transcribable_audio, transcribe_audio_file


def _parse_user_id(current_user: dict) -> UUID:
    return UUID(str(current_user["id"]))


def _ensure_participant(db: Session, conversation_id: UUID, user_id: UUID) -> None:
    if not chat_repo.is_participant(db, conversation_id, user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a participant in this conversation",
        )

def _validate_attachment_for_transcription(attachment: ChatAttachment) -> None:
    if attachment.attachment_type != "audio" and not is_transcribable_audio(attachment.mime_type):
        raise HTTPException(
            status_code=400,
            detail="Only audio attachments can be transcribed",
        )
    if not is_transcribable_audio(attachment.mime_type):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio type for transcription: {attachment.mime_type}",
        )
    file_path = resolve_attachment_file_path(attachment)
    if not file_path.is_file():
        raise_attachment_file_unavailable()


def _build_transcribe_response(
    attachment: ChatAttachment,
    *,
    already_transcribed: bool,
) -> TranscribeResponse:
    return TranscribeResponse(
        transcript=attachment.transcript or "",
        conversation_id=attachment.conversation_id,
        message_id=attachment.message_id,
        attachment_id=attachment.id,
        mime_type=attachment.mime_type,
        already_transcribed=already_transcribed,
        transcribed_at=attachment.transcribed_at,
    )


def transcribe_attachment_service(
    db: Session,
    current_user: dict,
    attachment_id: UUID,
    *,
    conversation_id: UUID | None = None,
    message_id: UUID | None = None,
) -> TranscribeResponse:
    user_id = _parse_user_id(current_user)
    attachment = chat_repo.get_attachment_by_id(db, attachment_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    _ensure_participant(db, attachment.conversation_id, user_id)

    if conversation_id and conversation_id != attachment.conversation_id:
        raise HTTPException(status_code=400, detail="Attachment does not belong to this conversation")

    if message_id:
        if attachment.message_id and attachment.message_id != message_id:
            raise HTTPException(status_code=400, detail="Attachment does not belong to this message")
        message = chat_repo.get_message_by_id(db, message_id)
        if not message:
            raise HTTPException(status_code=404, detail="Message not found")
        if message.conversation_id != attachment.conversation_id:
            raise HTTPException(status_code=400, detail="Message and attachment conversation mismatch")
        if message.attachment_id and message.attachment_id != attachment.id:
            raise HTTPException(status_code=400, detail="Message is linked to a different attachment")

    if attachment.transcript:
        return _build_transcribe_response(attachment, already_transcribed=True)

    _validate_attachment_for_transcription(attachment)
    file_path = resolve_attachment_file_path(attachment)
    transcript = transcribe_audio_file(str(file_path), attachment.mime_type)
    attachment = chat_repo.save_attachment_transcript(db, attachment, transcript)
    return _build_transcribe_response(attachment, already_transcribed=False)


def transcribe_message_service(
    db: Session,
    current_user: dict,
    message_id: UUID,
    *,
    conversation_id: UUID | None = None,
    attachment_id: UUID | None = None,
) -> TranscribeResponse:
    user_id = _parse_user_id(current_user)
    message = chat_repo.get_message_by_id(db, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    _ensure_participant(db, message.conversation_id, user_id)

    if conversation_id and conversation_id != message.conversation_id:
        raise HTTPException(status_code=400, detail="Message does not belong to this conversation")

    if message.is_deleted:
        raise HTTPException(status_code=400, detail="Deleted messages cannot be transcribed")

    resolved_attachment_id = attachment_id or message.attachment_id
    if not resolved_attachment_id:
        raise HTTPException(
            status_code=400,
            detail="No audio attachment found for this message. Provide attachment_id in the request body.",
        )

    return transcribe_attachment_service(
        db,
        current_user,
        resolved_attachment_id,
        conversation_id=message.conversation_id,
        message_id=message.id,
    )
