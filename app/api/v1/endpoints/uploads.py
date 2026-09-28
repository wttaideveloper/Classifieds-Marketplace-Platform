from uuid import UUID
from fastapi import APIRouter, Depends, File, Form, UploadFile, status, HTTPException
from sqlalchemy.orm import Session
from app.db.database import get_db
from fastapi.responses import FileResponse

from app.core.dependencies import get_current_user
from app.services.generic_upload_service import resolve_generic_upload_path, upload_generic_file_service

router = APIRouter(tags=["Uploads"])


@router.post(
    "/",
    status_code=status.HTTP_200_OK,
    summary="Upload a file (drag-and-drop media)",
    description=(
        "Generic file upload for training/course media authoring. "
        "Max 100 MB; allowed types: video/*, image/*, application/pdf, "
        "application/msword, application/vnd.openxmlformats-officedocument.*. "
        "Stored on local disk (no CDN configured in this deployment) — the "
        "returned url is served back via GET /api/v1/uploads/{folder}/{filename}."
    ),
)
def upload_file(
    file: UploadFile = File(..., description="File to upload"),
    folder: str | None = Form(None, description="Optional folder, e.g. 'trainings'. Defaults to 'general'."),
    field_key: str | None = Form(None, description="Training form media field key/id; enforces its resolved upload policy."),
    training_id: UUID | None = Form(None, description="Owned Training ID for historical form resolution."),
    purpose: str | None = Form(None, description="Training media purpose: image, lesson_video, lesson_pdf or lesson_document."),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if field_key or training_id or (folder or "").lower() in ("training", "trainings", "courses"):
        from app.services.training_form_media import resolve_upload_field, policy_cap, validate_media_bytes
        field = resolve_upload_field(db, current_user, field_key, training_id)
        if field:
            data = file.file.read(policy_cap(field) + 1)
            try:
                if len(data) > policy_cap(field):
                    raise HTTPException(413, "File exceeds configured maximum size")
                validate_media_bytes(field, data, file.content_type, purpose)
            finally:
                file.file.seek(0)
    return upload_generic_file_service(file, folder)


@router.get("/{folder}/{filename}", summary="Download a previously uploaded file")
def download_file(folder: str, filename: str):
    path = resolve_generic_upload_path(folder, filename)
    return FileResponse(path)
