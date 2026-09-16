"""POST /api/v1/uploads — generic drag-and-drop media upload (contract
section 6). Local-disk storage, no CDN wired up in this deployment."""

import io
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

from app.services.generic_upload_service import (
    MAX_UPLOAD_SIZE_BYTES,
    is_allowed_upload_type,
    resolve_generic_upload_path,
    upload_generic_file_service,
)


def _upload_file(content: bytes, filename: str, content_type: str) -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(content), headers={"content-type": content_type})


def test_allowed_types():
    assert is_allowed_upload_type("video/mp4") is True
    assert is_allowed_upload_type("image/png") is True
    assert is_allowed_upload_type("application/pdf") is True
    assert is_allowed_upload_type("application/msword") is True
    assert is_allowed_upload_type("application/vnd.openxmlformats-officedocument.wordprocessingml.document") is True


def test_disallowed_types():
    assert is_allowed_upload_type("application/x-msdownload") is False
    assert is_allowed_upload_type("text/html") is False
    assert is_allowed_upload_type(None) is False
    assert is_allowed_upload_type("") is False


def test_upload_rejects_unsupported_type():
    f = _upload_file(b"data", "virus.exe", "application/x-msdownload")
    with pytest.raises(HTTPException) as exc:
        upload_generic_file_service(f, folder="trainings")
    assert exc.value.status_code == 400


def test_upload_saves_file_and_returns_contract_shape(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.UPLOAD_DIR", str(tmp_path))

    f = _upload_file(b"hello world", "notes.pdf", "application/pdf")
    result = upload_generic_file_service(f, folder="trainings")

    assert result["name"] == "notes.pdf"
    assert result["size"] == len(b"hello world")
    assert result["type"] == "application/pdf"
    assert result["url"].startswith("/api/v1/uploads/trainings/")


def test_upload_defaults_folder_to_general(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.UPLOAD_DIR", str(tmp_path))

    f = _upload_file(b"hi", "a.png", "image/png")
    result = upload_generic_file_service(f, folder=None)
    assert "/api/v1/uploads/general/" in result["url"]


def test_upload_sanitizes_unsafe_folder_names(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.UPLOAD_DIR", str(tmp_path))

    f = _upload_file(b"hi", "a.png", "image/png")
    result = upload_generic_file_service(f, folder="../../etc")
    assert "/api/v1/uploads/general/" in result["url"] or ".." not in result["url"]
    assert ".." not in result["url"]


def test_upload_rejects_oversized_file(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr("app.services.generic_upload_service.MAX_UPLOAD_SIZE_BYTES", 10)

    f = _upload_file(b"x" * 100, "big.mp4", "video/mp4")
    with pytest.raises(HTTPException) as exc:
        upload_generic_file_service(f, folder="trainings")
    assert exc.value.status_code == 400
    assert "100" in exc.value.detail or "MB" in exc.value.detail or "maximum" in exc.value.detail.lower()


def test_oversized_upload_does_not_leave_a_partial_file_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr("app.services.generic_upload_service.MAX_UPLOAD_SIZE_BYTES", 10)

    f = _upload_file(b"x" * 100, "big.mp4", "video/mp4")
    with pytest.raises(HTTPException):
        upload_generic_file_service(f, folder="trainings")

    leftover = list((tmp_path / "generic" / "trainings").glob("*")) if (tmp_path / "generic" / "trainings").exists() else []
    assert leftover == []


def test_round_trip_upload_then_resolve_download_path(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.UPLOAD_DIR", str(tmp_path))

    f = _upload_file(b"content here", "doc.pdf", "application/pdf")
    result = upload_generic_file_service(f, folder="trainings")
    stored_filename = result["url"].rsplit("/", 1)[-1]

    resolved = resolve_generic_upload_path("trainings", stored_filename)
    assert resolved.is_file()
    assert resolved.read_bytes() == b"content here"


def test_resolve_download_path_404s_for_unknown_file(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.UPLOAD_DIR", str(tmp_path))
    with pytest.raises(HTTPException) as exc:
        resolve_generic_upload_path("trainings", "does-not-exist.pdf")
    assert exc.value.status_code == 404


def test_resolve_download_path_blocks_traversal_to_a_file_that_actually_exists(tmp_path, monkeypatch):
    """A weak version of this test (pointing at a path that simply doesn't
    exist) would pass even with the traversal guard removed, since is_file()
    alone returns False for missing paths — that proves nothing. This test
    creates a real file outside the upload root and confirms the guard
    specifically blocks reaching it, not just that 404 happens to fire."""
    monkeypatch.setattr("app.core.config.settings.UPLOAD_DIR", str(tmp_path / "uploads"))
    secret = tmp_path / "secret.txt"
    secret.write_text("do not serve me")

    with pytest.raises(HTTPException) as exc:
        resolve_generic_upload_path("trainings", "../../../secret.txt")
    assert exc.value.status_code == 403
