"""Offline downloadable lessons + auto-generated notes PDF."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.training_service import (
    generate_training_notes_pdf_service,
    get_lesson_download_service,
    list_downloadable_lessons_service,
)


def _training(**overrides):
    defaults = dict(
        title="Widgets 101",
        category="General",
        level="beginner",
        language="English",
        instructor_name="Jane Doe",
        description="Learn widgets.",
        requirements=None,
        target_audience=None,
        learning_objectives=[],
        notes_documents=[],
        offline_access_enabled=True,
        status="published",
        sections=[
            {
                "id": "sec-1",
                "title": "Getting Started",
                "lessons": [
                    {"id": "lesson-1", "title": "Intro video", "type": "video", "content_url": "https://cdn.x/intro.mp4", "is_downloadable": True},
                    {"id": "lesson-2", "title": "Reference PDF", "type": "pdf", "content_url": "https://cdn.x/ref.pdf", "is_downloadable": False},
                ],
            }
        ],
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


def test_list_downloadable_lessons_filters_to_flagged_lessons(monkeypatch):
    from app.services import training_service as svc

    training = _training()
    monkeypatch.setattr(svc, "_require_enrolled_or_staff", lambda db, tid, cu: training)

    result = list_downloadable_lessons_service(MagicMock(), uuid4(), {"role": "admin"})
    assert result["count"] == 1
    assert result["downloadable_lessons"][0]["lesson_id"] == "lesson-1"


def test_downloads_manifest_includes_uploaded_notes(monkeypatch):
    """Instructor-uploaded notes_documents must appear in the offline
    manifest alongside downloadable lessons, since notes are exactly the
    kind of thing a learner wants cached for offline reading."""
    from app.services import training_service as svc

    training = _training(notes_documents=[
        {"title": "Week 1 Handout", "url": "https://cdn.x/week1.pdf"},
        {"title": None, "url": "https://cdn.x/cheatsheet.pdf"},
    ])
    monkeypatch.setattr(svc, "_require_enrolled_or_staff", lambda db, tid, cu: training)

    result = list_downloadable_lessons_service(MagicMock(), uuid4(), {"role": "admin"})
    assert len(result["notes"]) == 2
    assert result["notes"][0] == {"title": "Week 1 Handout", "url": "https://cdn.x/week1.pdf"}
    assert result["notes"][1]["title"] == "Notes"  # fallback title when none given
    assert "notes.pdf" in result["notes_pdf_url"]


def test_list_downloadable_lessons_requires_offline_access_enabled(monkeypatch):
    from app.services import training_service as svc

    training = _training(offline_access_enabled=False)
    monkeypatch.setattr(svc, "_require_enrolled_or_staff", lambda db, tid, cu: training)

    with pytest.raises(HTTPException) as exc:
        list_downloadable_lessons_service(MagicMock(), uuid4(), {"role": "admin"})
    assert exc.value.status_code == 403


def test_get_lesson_download_rejects_non_downloadable_lesson(monkeypatch):
    from app.services import training_service as svc

    training = _training()
    monkeypatch.setattr(svc, "_require_enrolled_or_staff", lambda db, tid, cu: training)

    with pytest.raises(HTTPException) as exc:
        get_lesson_download_service(MagicMock(), uuid4(), "sec-1", "lesson-2", {"role": "admin"})
    assert exc.value.status_code == 403


def test_get_lesson_download_succeeds_for_downloadable_lesson(monkeypatch):
    from app.services import training_service as svc

    training = _training()
    monkeypatch.setattr(svc, "_require_enrolled_or_staff", lambda db, tid, cu: training)

    result = get_lesson_download_service(MagicMock(), uuid4(), "sec-1", "lesson-1", {"role": "admin"})
    assert result["content_url"] == "https://cdn.x/intro.mp4"


def test_generate_notes_pdf_produces_a_valid_pdf(monkeypatch):
    from app.services import training_service as svc

    training = _training(
        requirements="Basic math",
        target_audience="Beginners",
        learning_objectives=["Learn widgets", "Build one"],
    )
    monkeypatch.setattr(svc, "_require_enrolled_or_staff", lambda db, tid, cu: training)

    pdf_bytes = generate_training_notes_pdf_service(MagicMock(), uuid4(), {"role": "admin"})
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 100


def test_generate_notes_pdf_escapes_special_characters(monkeypatch):
    """Title/description containing <, >, & must not break reportlab's
    mini-markup Paragraph parser."""
    from app.services import training_service as svc

    training = _training(title="A & B <Course>", description="Learn <widgets> & gadgets")
    monkeypatch.setattr(svc, "_require_enrolled_or_staff", lambda db, tid, cu: training)

    pdf_bytes = generate_training_notes_pdf_service(MagicMock(), uuid4(), {"role": "admin"})
    assert pdf_bytes.startswith(b"%PDF-")
