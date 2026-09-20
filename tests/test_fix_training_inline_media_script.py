"""Verifies the one-time remediation script (scripts/fix_training_inline_media.py)
for already-persisted trainings whose sections/documents still contain inline
base64 data: URIs from before the write-time fix landed.

No database is used here — these tests exercise the script's pure
scan/convert functions directly, the same way fix_training(..., apply=...)
uses them, proving: (1) dry-run detects issues without writing anything,
(2) apply mode converts every affected field via the same tested
_persist_inline_media path used by normal training saves, and (3) running
twice is idempotent (second pass finds nothing left to do).
"""

import base64

from app.core.config import settings
from scripts.fix_training_inline_media import _scan_and_fix, _scan_and_fix_training_documents
import pytest

_FAKE_MP4 = b"fake mp4 bytes for script test"
_FAKE_PDF = b"fake pdf bytes for script test"
_MP4_URI = "data:video/mp4;base64," + base64.b64encode(_FAKE_MP4).decode()
_PDF_URI = "data:application/pdf;base64," + base64.b64encode(_FAKE_PDF).decode()


@pytest.fixture(autouse=True)
def _isolated_upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    yield


def _sections():
    return [{
        "id": "sec-1", "title": "Section 1",
        "lessons": [
            {"id": "lv1", "type": "video", "title": "Demo", "video_url": _MP4_URI},
            {"id": "lv2", "type": "pdf", "title": "Handbook", "content_url": _PDF_URI,
             "documents": [{"url": _PDF_URI, "name": "handbook.pdf"}]},
            {"id": "lv3", "type": "topic", "title": "Clean", "content_url": "https://cdn.example.com/already-fine.pdf"},
        ],
    }]


def test_dry_run_reports_without_mutating_anything():
    sections = _sections()
    new_sections, changes = _scan_and_fix(sections, apply=False)

    assert len(changes) == 3  # video_url, content_url, documents[]
    # Dry run: the returned copy is unchanged — still the original data URIs.
    lessons = {l["id"]: l for l in new_sections[0]["lessons"]}
    assert lessons["lv1"]["video_url"] == _MP4_URI
    assert lessons["lv2"]["content_url"] == _PDF_URI
    assert lessons["lv2"]["documents"][0]["url"] == _PDF_URI
    # Clean lesson never flagged.
    assert "lv3" not in " ".join(changes)


def test_apply_converts_every_affected_field():
    sections = _sections()
    new_sections, changes = _scan_and_fix(sections, apply=True)

    lessons = {l["id"]: l for l in new_sections[0]["lessons"]}
    assert not lessons["lv1"]["video_url"].startswith("data:")
    assert lessons["lv1"]["video_url"].endswith(".mp4")
    assert not lessons["lv2"]["content_url"].startswith("data:")
    assert lessons["lv2"]["content_url"].endswith(".pdf")
    assert not lessons["lv2"]["documents"][0]["url"].startswith("data:")
    # Already-clean URL untouched.
    assert lessons["lv3"]["content_url"] == "https://cdn.example.com/already-fine.pdf"


def test_apply_is_idempotent_second_pass_finds_nothing():
    sections = _sections()
    fixed_once, _ = _scan_and_fix(sections, apply=True)
    fixed_twice, changes_on_second_pass = _scan_and_fix(fixed_once, apply=True)

    assert changes_on_second_pass == []
    assert fixed_twice == fixed_once


def test_original_input_never_mutated_in_place():
    sections = _sections()
    original_video_url = sections[0]["lessons"][0]["video_url"]
    _scan_and_fix(sections, apply=True)
    assert sections[0]["lessons"][0]["video_url"] == original_video_url == _MP4_URI


def test_training_level_documents_dry_run_and_apply():
    documents = [{"url": _PDF_URI, "name": "Syllabus"}]

    _, changes = _scan_and_fix_training_documents(documents, apply=False)
    assert len(changes) == 1
    assert documents[0]["url"] == _PDF_URI  # untouched by dry run

    fixed, changes2 = _scan_and_fix_training_documents(documents, apply=True)
    assert not fixed[0]["url"].startswith("data:")
    assert fixed[0]["url"].endswith(".pdf")


def test_no_changes_when_nothing_inline():
    sections = [{"id": "sec-1", "lessons": [
        {"id": "lv1", "type": "topic", "content_url": "https://cdn.example.com/clean.pdf"},
    ]}]
    _, changes = _scan_and_fix(sections, apply=True)
    assert changes == []
