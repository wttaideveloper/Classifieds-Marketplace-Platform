from fastapi import APIRouter, Depends, File, Form, UploadFile, status
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
    current_user: dict = Depends(get_current_user),
):
    return upload_generic_file_service(file, folder)


@router.get("/{folder}/{filename}", summary="Download a previously uploaded file")
def download_file(folder: str, filename: str):
    path = resolve_generic_upload_path(folder, filename)
    return FileResponse(path)
