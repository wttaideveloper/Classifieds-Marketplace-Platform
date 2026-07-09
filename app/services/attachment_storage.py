"""Chat attachment filesystem storage and path resolution."""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, status

from app.core.config import settings
from app.models.chat_model import ChatAttachment

logger = logging.getLogger(__name__)

ATTACHMENT_FILE_UNAVAILABLE_CODE = "attachment_file_unavailable"


def ensure_upload_directory() -> Path:
    """Create the configured upload root if missing."""
    upload_root = settings.upload_dir_path
    upload_root.mkdir(parents=True, exist_ok=True)
    return upload_root


def storage_relative_key(conversation_id: UUID, stored_name: str) -> str:
    """Relative path stored in the database (under UPLOAD_DIR)."""
    return f"{conversation_id}/{stored_name}"


def _assert_under_upload_root(path: Path) -> Path:
    upload_root = settings.upload_dir_path
    resolved = path.resolve()
    try:
        resolved.relative_to(upload_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid attachment path",
        ) from exc
    return resolved


def _candidate_if_file(path: Path) -> Path | None:
    try:
        safe = _assert_under_upload_root(path)
    except HTTPException:
        return None
    return safe if safe.is_file() else None


def resolve_attachment_file_path(attachment: ChatAttachment) -> Path:
    """
    Resolve the on-disk path for an attachment record.

    Supports:
    - New relative keys: ``{conversation_id}/{stored_filename}``
    - Legacy absolute paths from older deployments
    - Reconstructed paths when only the stored filename differs by host prefix
    """
    upload_root = settings.upload_dir_path
    stored = (attachment.file_path or "").replace("\\", "/").strip()
    if not stored:
        return upload_root / "missing"

    relative = Path(stored)
    if not relative.is_absolute():
        found = _candidate_if_file(upload_root / relative)
        if found:
            return found

    legacy = Path(stored)
    if legacy.is_absolute():
        found = _candidate_if_file(legacy)
        if found:
            return found

    basename = legacy.name
    if basename and attachment.conversation_id:
        found = _candidate_if_file(upload_root / str(attachment.conversation_id) / basename)
        if found:
            return found

    marker = "/uploads/"
    idx = stored.find(marker)
    if idx != -1:
        suffix = stored[idx + len(marker) :]
        found = _candidate_if_file(upload_root / suffix)
        if found:
            return found

    if not relative.is_absolute():
        return _assert_under_upload_root(upload_root / relative)
    return legacy.resolve()


def attachment_file_is_available(attachment: ChatAttachment) -> bool:
    return resolve_attachment_file_path(attachment).is_file()


def raise_attachment_file_unavailable() -> None:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "message": "Attachment file is no longer available",
            "code": ATTACHMENT_FILE_UNAVAILABLE_CODE,
        },
    )
