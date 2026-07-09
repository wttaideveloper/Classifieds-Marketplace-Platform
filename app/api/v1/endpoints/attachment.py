from uuid import UUID

from fastapi import APIRouter, Body, Depends, File, Form, Path, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.schemas.chat_schema import (
    AttachmentDeleteResponse,
    AttachmentResponse,
    TranscribeRequest,
    TranscribeResponse,
)
from app.services.chat_attachment_service import (
    delete_attachment_service,
    download_attachment_service,
    get_attachment_service,
    upload_attachment_service,
)
from app.services.transcription_service import transcribe_attachment_service

router = APIRouter(tags=["Attachments"])


@router.post(
    "/upload",
    response_model=AttachmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload Attachment",
    description=(
        "Upload an image, document, audio, or video file. "
        "Validates file type, size, and user permissions."
    ),
)
async def upload_attachment(
    conversation_id: UUID = Form(..., description="Conversation to attach the file to."),
    file: UploadFile = File(..., description="File to upload."),
    attachment_type: str | None = Form(
        None,
        description="Optional type override: image, document, audio, video.",
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return upload_attachment_service(
        db,
        current_user,
        conversation_id=conversation_id,
        file=file,
        attachment_type=attachment_type,
    )


@router.get(
    "/{attachment_id}",
    summary="Get or Download Attachment",
    description="Returns attachment metadata. Append ?download=true to download the file.",
)
def get_attachment(
    attachment_id: UUID = Path(...),
    download: bool = Query(False, description="Set true to download the file."),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if download:
        attachment = download_attachment_service(db, current_user, attachment_id)
        return FileResponse(
            path=attachment.file_path,
            filename=attachment.file_name,
            media_type=attachment.mime_type,
        )
    return get_attachment_service(db, current_user, attachment_id)


@router.post(
    "/{attachment_id}/transcribe",
    response_model=TranscribeResponse,
    summary="Transcribe Audio Attachment",
    description=(
        "Convert a voice/audio attachment to text using speech-to-text. "
        "Supports audio/webm and audio/m4a. Returns a saved transcript on repeat requests. "
        "Does not create a new chat message."
    ),
)
def transcribe_attachment(
    attachment_id: UUID = Path(...),
    payload: TranscribeRequest | None = Body(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    body = payload or TranscribeRequest()
    return transcribe_attachment_service(
        db,
        current_user,
        attachment_id,
        conversation_id=body.conversation_id,
        message_id=body.message_id,
    )


@router.delete(
    "/{attachment_id}",
    response_model=AttachmentDeleteResponse,
    summary="Delete Attachment",
)
def delete_attachment(
    attachment_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return delete_attachment_service(db, current_user, attachment_id)
