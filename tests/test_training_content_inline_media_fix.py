"""Production bug: GET /api/v1/trainings/{id}/content returned raw base64
data URIs (e.g. "data:video/mp4;base64,AAAA...") in video_url/content_url/
videos[]/documents[].url instead of short HTTP(S) upload URLs like
http://.../api/v1/trainings/upload/<filename>.mp4.

Root cause: there is no server-side code anywhere that reads a video/PDF file
and base64-encodes it for the /content response — response_mappers.py and
training_curriculum.py are pure pass-through for these fields. The bytes were
being stored verbatim in Training.sections because normalize_curriculum /
normalize_authoring (the single write-time choke point used by both create
and update) never validated or converted an inline `data:` URI sent directly
by a client instead of uploading the file first via POST /trainings/upload.

Fix: normalize_curriculum's authoring-only branch (strict=True, which is
ONLY ever set by normalize_authoring — never by any read path) now detects a
`data:` URI in content_url/video_url/videos[]/documents[].url (and
normalize_authoring separately handles the training-level `documents` list)
and persists it exactly once via the EXISTING save_training_upload() helper
(the same one POST /trainings/upload already uses), replacing the field with
the resulting short upload URL. The read path (strict=False, used by
GET /content) is untouched and never touches file bytes.
"""

import base64
from uuid import uuid4

import pytest

from app.core.config import settings
from app.services.training_curriculum import (
    _persist_inline_media,
    normalize_authoring,
    normalize_curriculum,
)
from app.services.training_upload_service import resolve_training_upload

# Tiny, deliberately-fake "file" bytes — save_training_upload only validates
# extension/mime/size, never file content, so this is sufficient.
_FAKE_MP4_BYTES = b"\x00\x00\x00\x18ftypmp42fake video bytes for testing only"
_FAKE_PDF_BYTES = b"%PDF-1.4 fake pdf bytes for testing only"
_MP4_DATA_URI = "data:video/mp4;base64," + base64.b64encode(_FAKE_MP4_BYTES).decode()
_PDF_DATA_URI = "data:application/pdf;base64," + base64.b64encode(_FAKE_PDF_BYTES).decode()


@pytest.fixture(autouse=True)
def _isolated_upload_dir(tmp_path, monkeypatch):
    """Redirect training uploads to a throwaway directory for every test in
    this file — never touches the real uploads/ folder."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    yield


def _lesson_payload(**overrides):
    lesson = {"id": str(uuid4()), "type": "video", "title": "Lesson"}
    lesson.update(overrides)
    return {"sections": [{"id": "sec-1", "title": "Section 1", "lessons": [lesson]}]}


# --- 1 & 2: MP4/PDF fields return HTTP(S) upload URLs, not data URIs ---

def test_video_url_data_uri_becomes_http_upload_url_on_save():
    payload = _lesson_payload(video_url=_MP4_DATA_URI)
    result = normalize_authoring(payload)
    lesson = result["sections"][0]["lessons"][0]

    assert not lesson["video_url"].startswith("data:")
    assert lesson["video_url"].startswith("/api/v1/trainings/upload/") or lesson["video_url"].startswith("http")
    assert lesson["video_url"].endswith(".mp4")
    assert lesson["content_url"] == lesson["video_url"]


def test_content_url_pdf_data_uri_becomes_http_upload_url_on_save():
    payload = _lesson_payload(type="pdf", content_url=_PDF_DATA_URI)
    result = normalize_authoring(payload)
    lesson = result["sections"][0]["lessons"][0]

    assert not lesson["content_url"].startswith("data:")
    assert lesson["content_url"].endswith(".pdf")


# --- 3: videos[] entries return normal file URLs ---

def test_videos_array_data_uri_entries_become_upload_urls():
    payload = _lesson_payload(videos=[_MP4_DATA_URI, "https://cdn.example.com/already-hosted.mp4"])
    result = normalize_authoring(payload)
    lesson = result["sections"][0]["lessons"][0]

    assert not lesson["videos"][0].startswith("data:")
    assert lesson["videos"][0].endswith(".mp4")
    # Already-valid external URL passed through unchanged (item 5 of the ask).
    assert lesson["videos"][1] == "https://cdn.example.com/already-hosted.mp4"


# --- 4: documents[].url returns a normal file URL (lesson-level and training-level) ---

def test_lesson_documents_url_data_uri_becomes_upload_url():
    payload = _lesson_payload(
        type="topic",
        documents=[{"url": _PDF_DATA_URI, "name": "Handbook.pdf"}],
    )
    result = normalize_authoring(payload)
    lesson = result["sections"][0]["lessons"][0]

    assert not lesson["documents"][0]["url"].startswith("data:")
    assert lesson["documents"][0]["url"].endswith(".pdf")
    assert lesson["documents"][0]["name"] == "Handbook.pdf"  # other fields untouched


def test_training_level_documents_url_data_uri_becomes_upload_url():
    payload = {"documents": [{"url": _PDF_DATA_URI, "name": "Syllabus"}]}
    result = normalize_authoring(payload)

    assert not result["documents"][0]["url"].startswith("data:")
    assert result["documents"][0]["url"].endswith(".pdf")


# --- 5: existing valid HTTP/HTTPS/S3 URLs remain unchanged ---

def test_existing_http_url_passes_through_unchanged():
    existing_url = "http://13.207.85.164/api/v1/trainings/upload/abc123_lecture.mp4"
    payload = _lesson_payload(video_url=existing_url)
    result = normalize_authoring(payload)
    assert result["sections"][0]["lessons"][0]["video_url"] == existing_url


def test_existing_s3_url_passes_through_unchanged():
    existing_url = "https://my-bucket.s3.amazonaws.com/trainings/video.mp4?X-Amz-Signature=abc"
    payload = _lesson_payload(video_url=existing_url)
    result = normalize_authoring(payload)
    assert result["sections"][0]["lessons"][0]["video_url"] == existing_url


# --- 6: no data: URI ever appears in the final payload ---

def test_no_data_uri_strings_remain_anywhere_in_normalized_payload():
    payload = _lesson_payload(
        video_url=_MP4_DATA_URI,
        videos=[_MP4_DATA_URI],
        documents=[{"url": _PDF_DATA_URI}],
    )
    result = normalize_authoring(payload)
    import json
    serialized = json.dumps(result)
    assert "data:video/" not in serialized
    assert "data:application/pdf" not in serialized


# --- 7: the READ path never invokes the inline-media conversion (no file I/O on GET /content) ---

def test_read_path_never_persists_inline_media(monkeypatch):
    def _boom(value):
        raise AssertionError("read path must never call _persist_inline_media")

    monkeypatch.setattr(
        "app.services.training_curriculum._persist_inline_media", _boom
    )
    sections = [{"id": "sec-1", "title": "Section 1", "lessons": [
        {"id": "lv1", "type": "video", "video_url": _MP4_DATA_URI},
    ]}]
    # strict=False (the default) is what every read path (get_secure_training_content_service,
    # map_training_detail, etc.) uses — this must complete without touching the patched helper.
    result = normalize_curriculum(sections)
    # Read path is a pure pass-through: an already-bad stored value is
    # returned as-is (not silently dropped), since fixing historical rows is
    # a write-time concern (see docstring), not this function's job.
    assert result["sections"][0]["lessons"][0]["video_url"] == _MP4_DATA_URI


# --- 8: the returned URL is actually usable by the existing file-serving endpoint ---

def test_returned_upload_url_resolves_via_existing_file_serving_endpoint():
    payload = _lesson_payload(video_url=_MP4_DATA_URI)
    result = normalize_authoring(payload)
    url = result["sections"][0]["lessons"][0]["video_url"]
    stored_name = url.rsplit("/", 1)[-1]

    resolved_path = resolve_training_upload(stored_name)
    assert resolved_path.is_file()
    assert resolved_path.read_bytes() == _FAKE_MP4_BYTES


# --- direct unit coverage of the new helper itself ---

def test_persist_inline_media_passthrough_for_non_data_uri():
    assert _persist_inline_media("https://cdn.example.com/x.mp4") == "https://cdn.example.com/x.mp4"
    assert _persist_inline_media(None) is None
    assert _persist_inline_media("") == ""


def test_persist_inline_media_rejects_malformed_data_uri():
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        _persist_inline_media("data:video/mp4;base64,A")


# --- 9: existing training content behavior (non-media fields) is unchanged ---

def test_unrelated_lesson_fields_unaffected():
    payload = _lesson_payload(title="Intro to Ergonomics", is_mandatory=True, duration=15)
    result = normalize_authoring(payload)
    lesson = result["sections"][0]["lessons"][0]
    assert lesson["title"] == "Intro to Ergonomics"
    assert lesson["is_mandatory"] is True
    assert lesson["duration"] == 15


# --- 10: regression test for the exact reported scenario ---

def test_reported_training_scenario_video_and_pdf_no_longer_embed_bytes():
    """Mirrors the exact reported symptom: a training whose lessons carry an
    inline base64 video and PDF instead of upload URLs."""
    payload = {
        "sections": [{
            "id": "sec-1", "title": "Module 1",
            "lessons": [
                {"id": "lesson-video", "type": "video", "title": "Demo video", "video_url": _MP4_DATA_URI},
                {"id": "lesson-pdf", "type": "pdf", "title": "Handbook", "content_url": _PDF_DATA_URI,
                 "documents": [{"url": _PDF_DATA_URI, "name": "handbook.pdf"}]},
            ],
        }],
    }
    result = normalize_authoring(payload)
    video_lesson, pdf_lesson = result["sections"][0]["lessons"]

    for value in (video_lesson["video_url"], pdf_lesson["content_url"], pdf_lesson["documents"][0]["url"]):
        assert not value.startswith("data:")
        assert value.startswith("/api/v1/trainings/upload/") or value.startswith("http")

    import json
    assert "base64" not in json.dumps(result)
