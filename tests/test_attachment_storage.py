from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.models.chat_model import ChatAttachment
from app.services.attachment_storage import (
    ATTACHMENT_FILE_UNAVAILABLE_CODE,
    attachment_file_is_available,
    raise_attachment_file_unavailable,
    resolve_attachment_file_path,
    storage_relative_key,
)


_CONV_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
_STORED_NAME = "11111111-2222-3333-4444-555555555555_photo.jpg"


def _attachment(file_path: str) -> ChatAttachment:
    return ChatAttachment(
        id=uuid4(),
        conversation_id=_CONV_ID,
        uploaded_by=uuid4(),
        file_name="photo.jpg",
        file_path=file_path,
        mime_type="image/jpeg",
        file_size=100,
        attachment_type="image",
    )


def test_storage_relative_key():
    assert storage_relative_key(_CONV_ID, _STORED_NAME) == f"{_CONV_ID}/{_STORED_NAME}"


def test_resolve_relative_path(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.attachment_storage.settings.UPLOAD_DIR", str(tmp_path))
    conv_dir = tmp_path / str(_CONV_ID)
    conv_dir.mkdir(parents=True)
    file_path = conv_dir / _STORED_NAME
    file_path.write_bytes(b"data")

    attachment = _attachment(storage_relative_key(_CONV_ID, _STORED_NAME))
    resolved = resolve_attachment_file_path(attachment)

    assert resolved == file_path.resolve()
    assert attachment_file_is_available(attachment)


def test_resolve_legacy_absolute_path(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.attachment_storage.settings.UPLOAD_DIR", str(tmp_path))
    conv_dir = tmp_path / str(_CONV_ID)
    conv_dir.mkdir(parents=True)
    file_path = conv_dir / _STORED_NAME
    file_path.write_bytes(b"data")

    legacy_path = f"E:/old/project/uploads/{_CONV_ID}/{_STORED_NAME}"
    attachment = _attachment(legacy_path)
    resolved = resolve_attachment_file_path(attachment)

    assert resolved == file_path.resolve()


def test_resolve_legacy_absolute_path_when_file_exists(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.attachment_storage.settings.UPLOAD_DIR", str(tmp_path))
    other_root = tmp_path / "other"
    conv_dir = other_root / str(_CONV_ID)
    conv_dir.mkdir(parents=True)
    file_path = conv_dir / _STORED_NAME
    file_path.write_bytes(b"legacy")

    attachment = _attachment(str(file_path))
    resolved = resolve_attachment_file_path(attachment)

    assert resolved == file_path.resolve()


def test_raise_attachment_file_unavailable():
    with pytest.raises(HTTPException) as exc_info:
        raise_attachment_file_unavailable()

    assert exc_info.value.status_code == 410
    assert exc_info.value.detail["code"] == ATTACHMENT_FILE_UNAVAILABLE_CODE
    assert "no longer available" in exc_info.value.detail["message"]


def test_missing_file_returns_nonexistent_path(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.attachment_storage.settings.UPLOAD_DIR", str(tmp_path))
    attachment = _attachment(storage_relative_key(_CONV_ID, _STORED_NAME))

    resolved = resolve_attachment_file_path(attachment)
    assert not resolved.is_file()
    assert not attachment_file_is_available(attachment)
