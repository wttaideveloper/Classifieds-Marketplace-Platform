"""Generic file upload storage — POST /api/v1/uploads.

Local-disk storage under UPLOAD_DIR/generic/{folder}/, matching the
convention already used for chat attachments (attachment_storage.py).
No CDN is wired up in this deployment, so the returned `url` points at the
paired GET download endpoint rather than a public object-storage URL.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

MAX_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024  # 100 MB

_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_\-]+")
_READ_CHUNK_BYTES = 1024 * 1024


def _sanitize_segment(value: str | None, default: str) -> str:
    value = (value or "").strip()
    if not value:
        return default
    cleaned = _SAFE_SEGMENT.sub("_", value)
    return cleaned[:64] or default


def is_allowed_upload_type(content_type: str | None) -> bool:
    if not content_type:
        return False
    if content_type.startswith("video/") or content_type.startswith("image/"):
        return True
    if content_type == "application/pdf":
        return True
    if content_type == "application/msword":
        return True
    if content_type.startswith("application/vnd.openxmlformats-officedocument."):
        return True
    return False


def generic_upload_root() -> Path:
    from app.services.attachment_storage import ensure_upload_directory

    root = ensure_upload_directory() / "generic"
    root.mkdir(parents=True, exist_ok=True)
    return root


def upload_generic_file_service(file: UploadFile, folder: str | None) -> dict:
    content_type = file.content_type or ""
    if not is_allowed_upload_type(content_type):
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {content_type or 'unknown'}")

    folder_name = _sanitize_segment(folder, "general")
    target_dir = generic_upload_root() / folder_name
    target_dir.mkdir(parents=True, exist_ok=True)

    original_name = Path(file.filename or "upload").name
    ext = Path(original_name).suffix
    stored_name = f"{uuid.uuid4().hex}{ext}"
    dest_path = target_dir / stored_name

    size = 0
    try:
        with open(dest_path, "wb") as out:
            while True:
                chunk = file.file.read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_SIZE_BYTES:
                    raise HTTPException(status_code=400, detail="File exceeds maximum size of 100 MB")
                out.write(chunk)
    except HTTPException:
        dest_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to store file: {exc}") from exc

    return {
        "url": f"/api/v1/uploads/{folder_name}/{stored_name}",
        "name": original_name,
        "size": size,
        "type": content_type,
    }


def resolve_generic_upload_path(folder: str, filename: str) -> Path:
    root = generic_upload_root()
    candidate = (root / folder / filename).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Invalid upload path") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return candidate
