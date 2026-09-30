"""Mobile-team fixes:
1) is_enrolled / enrolment_status / rejection_reason on GET /trainings/{id} and
   rejection_reason on GET /trainings/my/enrolments — reject now sets a distinct
   'rejected' status (was silently reusing 'cancelled') with a stored reason.
2) GET /trainings/{id}/content returns structured 403 error codes instead of a
   generic 'Enrolled participants only' when pending/rejected/cancelled.
3) Notifications fire (without raising) on approve/reject/cancel.
4) GET/POST /trainings/{id}/reviews return participant_name.
Plus the same is_registered/registration_status idea on GET /events/{id}.
"""
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import training as training_routes
from app.core.dependencies import get_current_user
from app.db.database import Base, get_db
from app.models.enterprise_model import Enterprise
from app.models.training_model import (
    Training,
    TrainingAssessmentSubmission,
    TrainingAssignmentSubmission,
    TrainingEnrolment,
    TrainingLessonAttendance,
    TrainingLiveSession,
    TrainingOrder,
    TrainingProgress,
    TrainingReview,
    TrainingWaitlist,
)


@pytest.fixture(autouse=True)
def _no_real_notify(monkeypatch):
    # _safe_notify opens a real SessionLocal() (bound to the app's configured
    # DATABASE_URL, not the test's SQLite override) — stub it so tests stay
    # fast/offline and we can assert it was *called* without a live DB/SMTP.
    calls = []
    def fake_safe_notify(db, title, message, category, tenant_id, metadata, participant_email=None, channels=None):
        calls.append({"title": title, "message": message, "category": category, "metadata": metadata, "participant_email": participant_email})
    monkeypatch.setattr("app.services.notification_triggers._safe_notify", fake_safe_notify)
    return calls


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[m.__table__ for m in (
        Enterprise, Training, TrainingEnrolment, TrainingProgress, TrainingAssessmentSubmission,
        TrainingAssignmentSubmission, TrainingLessonAttendance, TrainingReview, TrainingOrder,
        TrainingLiveSession, TrainingWaitlist,
    )])
    sessions = sessionmaker(bind=engine)
    tenant_id = uuid4()
    admin = {"id": str(uuid4()), "role": "admin", "email": "admin@example.com", "tenant_id": str(tenant_id)}
    learner = {"id": str(uuid4()), "role": "learner", "email": "learner@example.com"}
    tid = uuid4()
    with sessions() as db:
        db.add(Training(
            id=tid, enterprise_id=uuid4(), tenant_id=tenant_id, title="Course", category="Wellness",
            status="published", delivery_mode="online", sections=[], assessments=[], assignments=[],
            requires_approval=True,
        ))
        db.commit()

    app = FastAPI()
    app.include_router(training_routes.router, prefix="/api/v1/trainings")

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: learner
    with TestClient(app) as client:
        yield sessions, client, admin, learner, tid
    engine.dispose()


def _as_admin(client, admin):
    client.app.dependency_overrides[get_current_user] = lambda: admin


def _as_learner(client, learner):
    client.app.dependency_overrides[get_current_user] = lambda: learner


# --- 1) is_enrolled / enrolment_status / rejection_reason ---

def test_never_enrolled_shape(setup):
    sessions, client, admin, learner, tid = setup
    resp = client.get(f"/api/v1/trainings/{tid}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_enrolled"] is False
    assert body["enrolment_status"] is None
    assert body["rejection_reason"] is None


def test_pending_approval_shape(setup):
    sessions, client, admin, learner, tid = setup
    with sessions() as db:
        db.add(TrainingEnrolment(training_id=tid, participant_name="Learner", participant_email=learner["email"], status="pending_approval"))
        db.commit()
    body = client.get(f"/api/v1/trainings/{tid}").json()
    assert body["is_enrolled"] is True
    assert body["enrolment_status"] == "pending_approval"
    assert body["rejection_reason"] is None


def test_enrolled_shape(setup):
    sessions, client, admin, learner, tid = setup
    with sessions() as db:
        db.add(TrainingEnrolment(training_id=tid, participant_name="Learner", participant_email=learner["email"], status="enrolled"))
        db.commit()
    body = client.get(f"/api/v1/trainings/{tid}").json()
    assert body["is_enrolled"] is True
    assert body["enrolment_status"] == "enrolled"


def test_cancelled_shape(setup):
    sessions, client, admin, learner, tid = setup
    with sessions() as db:
        db.add(TrainingEnrolment(training_id=tid, participant_name="Learner", participant_email=learner["email"], status="cancelled"))
        db.commit()
    body = client.get(f"/api/v1/trainings/{tid}").json()
    assert body["is_enrolled"] is False
    assert body["enrolment_status"] == "cancelled"


def test_reject_sets_distinct_rejected_status_with_reason(setup, _no_real_notify):
    sessions, client, admin, learner, tid = setup
    with sessions() as db:
        enrol = TrainingEnrolment(training_id=tid, participant_name="Learner", participant_email=learner["email"], status="pending_approval")
        db.add(enrol)
        db.commit()
        enrol_id = str(enrol.id)

    _as_admin(client, admin)
    resp = client.post(f"/api/v1/trainings/{tid}/enrolments/{enrol_id}/approve", json={"action": "reject", "reason": "Seat full"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"

    _as_learner(client, learner)
    body = client.get(f"/api/v1/trainings/{tid}").json()
    assert body["is_enrolled"] is False
    assert body["enrolment_status"] == "rejected"
    assert body["rejection_reason"] == "Seat full"

    # Notification fired for the reject action.
    categories = [c["category"] for c in _no_real_notify]
    assert "enrolment_rejected" in categories
    rejected_call = next(c for c in _no_real_notify if c["category"] == "enrolment_rejected")
    assert "Seat full" in rejected_call["message"]
    assert rejected_call["participant_email"] == learner["email"]


def test_approve_notifies_and_clears_prior_rejection_reason(setup, _no_real_notify):
    sessions, client, admin, learner, tid = setup
    with sessions() as db:
        enrol = TrainingEnrolment(
            training_id=tid, participant_name="Learner", participant_email=learner["email"],
            status="rejected", rejection_reason="Seat full",
        )
        db.add(enrol)
        db.commit()
        enrol_id = str(enrol.id)

    _as_admin(client, admin)
    resp = client.post(f"/api/v1/trainings/{tid}/enrolments/{enrol_id}/approve", json={"action": "approve"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "enrolled"

    _as_learner(client, learner)
    body = client.get(f"/api/v1/trainings/{tid}").json()
    assert body["enrolment_status"] == "enrolled"
    assert body["rejection_reason"] is None
    assert "enrolment_approved" in [c["category"] for c in _no_real_notify]


def test_rejected_learner_can_re_enrol(setup, _no_real_notify):
    """A rejected enrolment must not permanently block re-enrolling — same
    behavior duplicate-enrolment guards already gave 'cancelled'."""
    sessions, client, admin, learner, tid = setup
    with sessions() as db:
        db.add(TrainingEnrolment(training_id=tid, participant_name="Learner", participant_email=learner["email"], status="rejected", rejection_reason="Seat full"))
        db.commit()

    _as_learner(client, learner)
    resp = client.post(f"/api/v1/trainings/{tid}/enrol", json={})
    assert resp.status_code in (200, 201), resp.text


def test_my_enrolments_includes_status_and_rejection_reason(setup, _no_real_notify):
    sessions, client, admin, learner, tid = setup
    with sessions() as db:
        db.add(TrainingEnrolment(training_id=tid, participant_name="Learner", participant_email=learner["email"], status="rejected", rejection_reason="Seat full"))
        db.commit()

    _as_learner(client, learner)
    resp = client.get("/api/v1/trainings/my/enrolments")
    assert resp.status_code == 200, resp.text
    row = resp.json()[0]
    assert row["status"] == "rejected"
    assert row["rejection_reason"] == "Seat full"


# --- 2) Content API structured errors ---

def test_content_pending_approval_returns_structured_403(setup):
    sessions, client, admin, learner, tid = setup
    with sessions() as db:
        db.add(TrainingEnrolment(training_id=tid, participant_name="Learner", participant_email=learner["email"], status="pending_approval"))
        db.commit()

    resp = client.get(f"/api/v1/trainings/{tid}/content")
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "ENROLMENT_PENDING_APPROVAL"
    assert "not approved" in detail["message"].lower()


def test_content_rejected_returns_structured_403_with_reason(setup, _no_real_notify):
    sessions, client, admin, learner, tid = setup
    with sessions() as db:
        enrol = TrainingEnrolment(training_id=tid, participant_name="Learner", participant_email=learner["email"], status="pending_approval")
        db.add(enrol)
        db.commit()
        enrol_id = str(enrol.id)

    _as_admin(client, admin)
    client.post(f"/api/v1/trainings/{tid}/enrolments/{enrol_id}/approve", json={"action": "reject", "reason": "Seat full"})

    _as_learner(client, learner)
    resp = client.get(f"/api/v1/trainings/{tid}/content")
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "ENROLMENT_NOT_ACTIVE"
    assert detail["enrolment_status"] == "rejected"
    assert detail["rejection_reason"] == "Seat full"


def test_content_cancelled_returns_structured_403(setup):
    sessions, client, admin, learner, tid = setup
    with sessions() as db:
        db.add(TrainingEnrolment(training_id=tid, participant_name="Learner", participant_email=learner["email"], status="cancelled"))
        db.commit()

    resp = client.get(f"/api/v1/trainings/{tid}/content")
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "ENROLMENT_NOT_ACTIVE"
    assert detail["enrolment_status"] == "cancelled"


def test_content_never_enrolled_returns_plain_403(setup):
    sessions, client, admin, learner, tid = setup
    resp = client.get(f"/api/v1/trainings/{tid}/content")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Enrolled participants only"


def test_content_approved_learner_gets_200(setup):
    sessions, client, admin, learner, tid = setup
    with sessions() as db:
        db.add(TrainingEnrolment(training_id=tid, participant_name="Learner", participant_email=learner["email"], status="enrolled"))
        db.commit()
    resp = client.get(f"/api/v1/trainings/{tid}/content")
    assert resp.status_code == 200, resp.text


# --- 3) Notifications on cancel ---

def test_cancel_notifies(setup, _no_real_notify):
    sessions, client, admin, learner, tid = setup
    with sessions() as db:
        enrol = TrainingEnrolment(training_id=tid, participant_name="Learner", participant_email=learner["email"], status="enrolled")
        db.add(enrol)
        db.commit()
        enrol_id = str(enrol.id)

    resp = client.delete(f"/api/v1/trainings/{tid}/enrolments/{enrol_id}")
    assert resp.status_code == 200, resp.text
    assert "enrolment_cancelled" in [c["category"] for c in _no_real_notify]


# --- 4) Reviews — participant_name ---

def test_review_create_and_list_include_participant_name(setup):
    sessions, client, admin, learner, tid = setup
    with sessions() as db:
        db.add(TrainingEnrolment(training_id=tid, participant_name="Suresh Inti", participant_email=learner["email"], status="enrolled"))
        db.commit()

    create_resp = client.post(f"/api/v1/trainings/{tid}/reviews", json={"rating": 4, "comment": "Good"})
    assert create_resp.status_code in (200, 201), create_resp.text
    assert create_resp.json()["participant_name"] == "Suresh Inti"
    assert create_resp.json()["participant_email"] == learner["email"]

    list_resp = client.get(f"/api/v1/trainings/{tid}/reviews")
    assert list_resp.status_code == 200, list_resp.text
    review = list_resp.json()["reviews"][0]
    assert review["participant_name"] == "Suresh Inti"


def test_review_list_name_is_null_when_no_matching_enrolment(setup):
    """Defensive: a review whose participant_email has no enrolment row on
    this training (shouldn't normally happen — reviews require enrolment —
    but covers stale/orphaned data) must not 500, just omit the name."""
    sessions, client, admin, learner, tid = setup
    with sessions() as db:
        db.add(TrainingReview(training_id=tid, participant_email="orphan@example.com", rating="5", comment="x"))
        db.commit()
    resp = client.get(f"/api/v1/trainings/{tid}/reviews")
    assert resp.status_code == 200, resp.text
    review = resp.json()["reviews"][0]
    assert review["participant_name"] is None
