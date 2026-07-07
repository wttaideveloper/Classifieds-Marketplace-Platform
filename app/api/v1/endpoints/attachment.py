from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.schemas.chat_schema import AttachmentDeleteResponse, AttachmentResponse
from app.services.chat_attachment_service import (
    delete_attachment_service,
    download_attachment_service,
    get_attachment_service,
    upload_attachment_service,
)

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
