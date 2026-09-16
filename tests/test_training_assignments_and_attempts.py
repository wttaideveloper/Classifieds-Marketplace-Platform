"""Three fixes:
1. GET /{training_id}/assignments was gated to admin/provider only, giving
   any learner {"detail": "Not authorized"} — opened up to any authenticated
   user, matching the existing list_assessments convention.
2. Assessment/assignment submit responses now surface attempts_made
   (and attempts_allowed for assessments) — previously computed internally
   for attempt-limit enforcement but never returned to the caller.
3. GET .../assessments/{aid}/submissions/{sid}/review response schema
   documented in Swagger via AssessmentReviewResponse.
"""

from unittest.mock import MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import training
from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.services import training_service as service


def _app_with_user(role, db):
    app = FastAPI()
    app.include_router(training.router, prefix="/api/v1/trainings")
    app.dependency_overrides[get_current_user] = lambda: {"email": "learner@example.com", "role": role}
    app.dependency_overrides[get_db] = lambda: db
    return app


# --- 1. GET /assignments authorization ---

def test_list_assignments_no_longer_rejects_a_plain_authenticated_learner(monkeypatch):
    """Reproduces the reported bug: a non-admin/provider user hit
    'Not authorized' just listing assignments — there's no sensitive data
    in an assignment definition, so any authenticated user should see it,
    same as list_assessments."""
    training_obj = MagicMock()
    training_obj.assignments = [{"id": "assign-1", "title": "Week 1 Practical", "type": "assignment",
                                  "instructions": None, "due_date": None, "max_score": 10,
                                  "accepted_file_types": None, "allow_late_submissions": False}]
    monkeypatch.setattr(service, "get_training_by_id", lambda db, t: training_obj)

    app = _app_with_user("participant", MagicMock())
    response = TestClient(app).get(f"/api/v1/trainings/{uuid4()}/assignments")

    assert response.status_code == 200
    assert response.json()[0]["title"] == "Week 1 Practical"


def test_list_assignments_still_works_for_admin(monkeypatch):
    training_obj = MagicMock()
    training_obj.assignments = []
    monkeypatch.setattr(service, "get_training_by_id", lambda db, t: training_obj)

    app = _app_with_user("admin", MagicMock())
    response = TestClient(app).get(f"/api/v1/trainings/{uuid4()}/assignments")
    assert response.status_code == 200
    assert response.json() == []


# --- 2. attempts_made / attempts_allowed on submit ---

def test_submit_assessment_service_returns_attempts_made_and_allowed(monkeypatch):
    tid = uuid4()
    training_obj = MagicMock()
    assessment = {
        "id": "a1",
        "attempts_allowed": 3,
        "pass_percent": 50,
        "questions": [{"id": "q1", "question_type": "mcq", "correct_answer": "Mars", "points": 1}],
    }
    training_obj.assessments = [assessment]
    monkeypatch.setattr(service, "_get_training_or_404", lambda db, t: training_obj)

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None  # no access-expiry enrolment
    db.query.return_value.filter.return_value.count.return_value = 1  # this participant already has 1 prior submission

    payload = MagicMock()
    payload.answers = [{"question_id": "q1", "answer": "Mars"}]
    payload.started_at = None

    result = service.submit_assessment_service(db, tid, "a1", payload, participant_email="learner@example.com")

    assert result["attempts_made"] == 2  # 1 prior + this one
    assert result["attempts_allowed"] == 3


def test_submit_assessment_service_attempts_allowed_is_null_when_unlimited(monkeypatch):
    tid = uuid4()
    training_obj = MagicMock()
    assessment = {"id": "a1", "questions": []}  # no attempt_limit / attempts_allowed declared
    training_obj.assessments = [assessment]
    monkeypatch.setattr(service, "_get_training_or_404", lambda db, t: training_obj)

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.count.return_value = 0

    payload = MagicMock()
    payload.answers = []
    payload.started_at = None

    result = service.submit_assessment_service(db, tid, "a1", payload, participant_email="learner@example.com")
    assert result["attempts_made"] == 1
    assert result["attempts_allowed"] is None


def test_submit_assignment_service_returns_attempts_made(monkeypatch):
    tid = uuid4()
    training_obj = MagicMock()
    training_obj.assignments = [{"id": "as1", "accepted_file_types": [], "due_date": None, "allow_late_submissions": False}]
    monkeypatch.setattr(service, "_get_training_or_404", lambda db, t: training_obj)

    from datetime import datetime

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None  # no access-expiry enrolment
    db.query.return_value.filter.return_value.count.return_value = 2  # this submission + 1 prior
    db.refresh.side_effect = lambda obj: setattr(obj, "submitted_at", datetime(2026, 9, 16))

    payload = MagicMock()
    payload.file_url = None
    payload.files = []
    payload.submission_text = "done"

    result = service.submit_assignment_service(db, tid, "as1", payload, participant_email="learner@example.com")
    assert result["attempts_made"] == 2


def test_get_assessment_service_reports_attempts_made_for_current_user(monkeypatch):
    tid = uuid4()
    training_obj = MagicMock()
    training_obj.assessments = [{"id": "a1", "attempt_limit": 3, "questions": []}]
    monkeypatch.setattr(service, "_get_training_or_404", lambda db, t: training_obj)

    db = MagicMock()
    db.query.return_value.filter.return_value.count.return_value = 1

    result = service.get_assessment_service(db, tid, "a1", {"email": "learner@example.com", "role": "participant"})
    assert result["attempts_made"] == 1
    assert result["attempts_allowed"] == 3


def test_get_assessment_service_attempts_made_zero_without_identity(monkeypatch):
    tid = uuid4()
    training_obj = MagicMock()
    training_obj.assessments = [{"id": "a1", "questions": []}]
    monkeypatch.setattr(service, "_get_training_or_404", lambda db, t: training_obj)

    result = service.get_assessment_service(MagicMock(), tid, "a1", None)
    assert result["attempts_made"] == 0
    assert result["attempts_allowed"] is None


# --- 3. review response schema matches the service's actual return shape ---

def test_assessment_review_response_schema_accepts_every_key_the_service_returns():
    from app.schemas.training_schema import AssessmentReviewResponse

    service_return = {
        "submission_id": "3c7c3e2a-9b1a-4c2e-8e2a-1a2b3c4d5e6f",
        "assessment_id": "a1",
        "score": "1",
        "passed": True,
        "review": [
            {"question_id": "q1", "question_text": "Q?", "given": "Mars", "correct": "Mars", "explanation": None, "points": 1},
        ],
        "level": "module",
    }
    validated = AssessmentReviewResponse.model_validate(service_return)
    assert validated.model_dump(mode="json") == service_return


def test_get_assessment_result_service_matches_review_response_schema(monkeypatch):
    """End-to-end: the actual dict returned by get_assessment_result_service
    validates cleanly against AssessmentReviewResponse (the schema now wired
    as response_model on the review endpoint) — proves the documented
    schema isn't fiction."""
    from app.schemas.training_schema import AssessmentReviewResponse
    from app.models.training_model import TrainingAssessmentSubmission

    tid = uuid4()
    training_obj = MagicMock()
    training_obj.assessments = [{
        "id": "a1", "level": "module",
        "questions": [{"id": "q1", "question_text": "Which planet?", "correct_answer": "Mars", "explanation": "iron oxide", "points": 1}],
    }]
    monkeypatch.setattr(service, "_get_training_or_404", lambda db, t: training_obj)

    sub = MagicMock()
    sub.id = "3c7c3e2a-9b1a-4c2e-8e2a-1a2b3c4d5e6f"
    sub.participant_email = "learner@example.com"
    sub.answers = [{"question_id": "q1", "answer": "Mars"}]
    sub.score = "1"
    sub.passed = True

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = sub

    result = service.get_assessment_result_service(db, tid, "a1", str(sub.id), {"email": "learner@example.com", "role": "participant"})
    AssessmentReviewResponse.model_validate(result)  # raises if the real shape ever drifts from the documented schema
