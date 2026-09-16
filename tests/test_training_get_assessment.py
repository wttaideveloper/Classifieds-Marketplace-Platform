"""GET /{training_id}/assessments/{aid} — fetch one assessment by id, and
the submit-assessment response_model wiring (must not drop fields the
service actually returns)."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services import training_service

ASSESSMENT = {
    "id": "42aec07b-0000-0000-0000-000000000001",
    "title": "Module 1 Quiz",
    "questions": [
        {
            "id": "q1",
            "question_text": "Which planet is known as the Red Planet?",
            "question_type": "mcq",
            "options": ["Earth", "Mars", "Jupiter"],
            "correct_answer": "Mars",
            "points": 1,
            "explanation": "Iron oxide gives Mars its color.",
        }
    ],
}


def _training_with_assessment(monkeypatch, assessments=(ASSESSMENT,)):
    training = MagicMock()
    training.assessments = list(assessments)
    monkeypatch.setattr(training_service, "get_training_by_id", lambda db, t: training)
    return training


# --- get_assessment_service ---

def test_get_assessment_service_returns_full_assessment_for_admin(monkeypatch):
    _training_with_assessment(monkeypatch)
    result = training_service.get_assessment_service(MagicMock(), uuid4(), ASSESSMENT["id"], {"role": "admin"})
    assert result["id"] == ASSESSMENT["id"]
    assert result["questions"][0]["correct_answer"] == "Mars"
    assert result["questions"][0]["explanation"] == "Iron oxide gives Mars its color."


def test_get_assessment_service_strips_correct_answer_for_learner(monkeypatch):
    _training_with_assessment(monkeypatch)
    result = training_service.get_assessment_service(MagicMock(), uuid4(), ASSESSMENT["id"], {"role": "participant"})
    q = result["questions"][0]
    assert "correct_answer" not in q
    assert "explanation" not in q
    assert q["question_text"] == ASSESSMENT["questions"][0]["question_text"]


def test_get_assessment_service_strips_for_anonymous_user(monkeypatch):
    _training_with_assessment(monkeypatch)
    result = training_service.get_assessment_service(MagicMock(), uuid4(), ASSESSMENT["id"], None)
    assert "correct_answer" not in result["questions"][0]


def test_get_assessment_service_provider_sees_correct_answer(monkeypatch):
    _training_with_assessment(monkeypatch)
    result = training_service.get_assessment_service(MagicMock(), uuid4(), ASSESSMENT["id"], {"role": "provider"})
    assert result["questions"][0]["correct_answer"] == "Mars"


def test_get_assessment_service_404_for_unknown_id(monkeypatch):
    _training_with_assessment(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        training_service.get_assessment_service(MagicMock(), uuid4(), "does-not-exist", {"role": "admin"})
    assert exc.value.status_code == 404


def test_get_assessment_service_does_not_mutate_stored_assessments(monkeypatch):
    """Stripping correct_answer for a learner must operate on a copy —
    must never corrupt the ORM-tracked assessments list in place."""
    training = _training_with_assessment(monkeypatch)
    training_service.get_assessment_service(MagicMock(), uuid4(), ASSESSMENT["id"], {"role": "participant"})
    assert training.assessments[0]["questions"][0]["correct_answer"] == "Mars"


# --- submit response_model is a safe superset of what the service returns ---

def test_assessment_submit_response_schema_accepts_every_key_the_service_returns():
    from app.schemas.training_schema import AssessmentSubmitResponse

    service_return = {
        "score": 4,
        "passed": False,
        "total_points": 10,
        "feedback": "Pending manual evaluation",
        "assessment_id": "42aec07b-0000-0000-0000-000000000001",
        "submission_id": "3c7c3e2a-9b1a-4c2e-8e2a-1a2b3c4d5e6f",
        "publication": "immediate",
        "needs_manual": True,
    }
    validated = AssessmentSubmitResponse.model_validate(service_return)
    dumped = validated.model_dump(mode="json")
    # Every key the service actually returns survives response_model filtering.
    assert dumped == service_return
