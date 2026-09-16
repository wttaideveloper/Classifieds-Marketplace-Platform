"""Training media upload filesystem storage and path resolution."""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger(__name__)

# Sub-directory under UPLOAD_DIR where training media lives.
TRAINING_UPLOAD_SUBDIR = "training"

# file extension allowlists per purpose
ALLOWED_EXTENSIONS: dict[str, set[str]] = {
    "lesson_video": {"mp4", "webm", "mov", "m4v", "mkv", "ogg", "ogv", "avi", "3gp", "mpeg", "mpg"},
    "lesson_pdf": {"pdf"},
    "lesson_document": {"pdf", "doc", "docx", "xls", "xlsx", "txt", "ppt", "pptx"},
    "image": {"jpeg", "jpg", "png", "gif", "webp"},
    "audio": {"mp3", "m4a", "wav", "ogg", "oga", "opus", "aac"},
}

# MIME-type prefix allowlists per purpose
ALLOWED_MIME_PREFIXES: dict[str, tuple[str, ...]] = {
    # .ogg/.ogv is a dual container (video/ogg or audio/ogg) — accept both for video uploads.
    "lesson_video": ("video/", "audio/ogg", "audio/opus", "audio/vorbis"),
    "lesson_pdf": ("application/pdf",),
    "lesson_document": (
        "application/",
        "text/",
        "application/vnd.ms-",
        "application/vnd.openxmlformats-",
    ),
    "image": ("image/",),
    "audio": ("audio/", "video/ogg"),
}

# Size caps (bytes) per purpose — matches MAX_*_SIZE_MB config settings
MAX_SIZE_BYTES: dict[str, int] = {
    "lesson_video": settings.MAX_VIDEO_SIZE_MB * 1024 * 1024,
    "lesson_pdf": settings.MAX_DOCUMENT_SIZE_MB * 1024 * 1024,
    "lesson_document": settings.MAX_DOCUMENT_SIZE_MB * 1024 * 1024,
    "image": settings.MAX_IMAGE_SIZE_MB * 1024 * 1024,
    "audio": settings.MAX_AUDIO_SIZE_MB * 1024 * 1024,
}

DEFAULT_PURPOSE = "lesson_document"

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_STORED_NAME_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_.+$")


def training_upload_root() -> Path:
    """Absolute UPLOAD_DIR/training directory, created if missing."""
    root = settings.upload_dir_path / TRAINING_UPLOAD_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def _normalize_stored_name(name: str) -> str:
    return _SAFE_NAME_RE.sub("_", name).strip("._ ").lower()


_VIDEO_EXTS = {"mp4", "webm", "mov", "m4v", "mkv", "ogg", "ogv", "avi", "3gp", "mpeg", "mpg"}


def _infer_purpose(content_type: str | None, filename: str | None) -> str:
    """Infer upload purpose when the client omits it (endpoint documents this).

    video/* (or a video extension, incl. dual-container .ogg/.ogv) -> lesson_video (100 MB).
    application/pdf / .pdf -> lesson_pdf. image/* -> image. audio/* -> audio.
    Everything else falls back to lesson_document (25 MB).
    """
    mime = (content_type or "").lower().split(";")[0].strip()
    ext = ((filename or "").rsplit(".", 1)[-1] if "." in (filename or "") else "").lower()
    if mime.startswith("video/") or ext in _VIDEO_EXTS:
        # audio/ogg with a video-ish filename (.ogg/.ogv) is treated as video
        # so browser-recorded clips upload under the 100 MB lesson_video cap.
        if mime.startswith("audio/") and ext not in {"ogg", "ogv"}:
            return "audio"
        return "lesson_video"
    if mime == "application/pdf" or ext == "pdf":
        return "lesson_pdf"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    return DEFAULT_PURPOSE


def save_training_upload(
    file_bytes: bytes,
    filename: str,
    content_type: str | None,
    purpose: str | None,
    db: Session | None = None,
    current_user: dict | None = None,
) -> dict:
    """Validate and persist a training media file.

    Returns metadata dict: {url, name, size, type, purpose}.
    """
    purpose = (purpose or _infer_purpose(content_type, filename)).lower()
    if purpose not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported upload purpose '{purpose}'. Allowed: {sorted(ALLOWED_EXTENSIONS)}",
        )

    size = len(file_bytes)
    cap = MAX_SIZE_BYTES[purpose]
    if size > cap:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large for {purpose} (max {cap // (1024 * 1024)} MB)",
        )

    ext = (Path(filename or "").suffix or "").lstrip(".").lower()
    if ext not in ALLOWED_EXTENSIONS[purpose]:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"File type '.{ext}' not allowed for {purpose}. Allowed: {sorted(ALLOWED_EXTENSIONS[purpose])}",
        )

    mime = (content_type or "").lower()
    allowed_mimes = ALLOWED_MIME_PREFIXES[purpose]
    if mime and not mime.startswith(allowed_mimes):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Content type '{content_type}' not allowed for {purpose}",
        )

    if not file_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    safe_name = _normalize_stored_name(filename or f"upload.{ext}") or f"file.{ext}"
    stored_name = f"{uuid.uuid4()}_{safe_name}"
    target = training_upload_root() / stored_name
    target.write_bytes(file_bytes)

    base = (settings.PUBLIC_API_BASE_URL or "").rstrip("/")
    url = f"{base}/api/v1/trainings/upload/{stored_name}"

    logger.info("Saved training media %s (%s bytes, %s)", stored_name, size, purpose)
    return {
        "url": url,
        "name": filename or safe_name,
        "size": size,
        "type": content_type or "application/octet-stream",
        "purpose": purpose,
    }


def resolve_training_upload(stored_name: str) -> Path:
    """Resolve a stored name to an on-disk file, guarding against path traversal."""
    if not _STORED_NAME_RE.match(stored_name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid training media file name",
        )
    root = training_upload_root()
    candidate = root / stored_name
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid training media file name",
        ) from exc
    if not candidate.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Training media file not found",
        )
    return candidate