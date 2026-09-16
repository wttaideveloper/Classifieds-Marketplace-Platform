"""POST /trainings/{id}/progress/complete-lesson — the body was previously a
bare `dict`, so Swagger showed {"additionalProp1": {}} for the request and
"string" for the response. This documents the real accepted/returned shape."""

import pytest
from pydantic import ValidationError

from app.schemas.training_schema import TrainingCompleteLessonRequest, TrainingCompleteLessonResponse


def test_lesson_id_is_required():
    with pytest.raises(ValidationError):
        TrainingCompleteLessonRequest()


def test_lesson_id_accepted_directly():
    req = TrainingCompleteLessonRequest(lesson_id="lesson-1")
    assert req.lesson_id == "lesson-1"
    assert req.participant_email is None


def test_legacy_id_field_still_populates_lesson_id():
    """Backward compatibility: the handler used to accept payload['id'] as a
    fallback for payload['lesson_id'] — that must keep working now that the
    body is a typed model instead of a bare dict."""
    req = TrainingCompleteLessonRequest.model_validate({"id": "lesson-1"})
    assert req.lesson_id == "lesson-1"


def test_participant_email_optional_and_passthrough():
    req = TrainingCompleteLessonRequest(lesson_id="lesson-1", participant_email="learner@example.com")
    assert req.participant_email == "learner@example.com"


def test_response_shape_matches_the_real_service_return_value():
    """Mirrors the exact dict shape complete_lesson_service returns."""
    payload = {
        "lesson_id": "lesson-1",
        "overall_percent": 65.0,
        "lessons_done": 13,
        "total_lessons": 20,
        "mandatory_done": 10,
        "mandatory_total": 12,
        "completed_at": None,
        "certificate_url": None,
        "resume_lesson": "lesson-1",
    }
    resp = TrainingCompleteLessonResponse.model_validate(payload)
    assert resp.overall_percent == 65.0
    assert resp.resume_lesson == "lesson-1"


def test_response_requires_all_progress_counters():
    with pytest.raises(ValidationError):
        TrainingCompleteLessonResponse(lesson_id="x", resume_lesson="x")
