from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import training as routes
from app.core.dependencies import get_current_user
from app.db.database import Base, get_db
from app.models.enterprise_model import Enterprise
from app.models.training_model import Training, TrainingEnrolment, TrainingLessonAttendance, TrainingProgress, TrainingAssessmentSubmission, TrainingAssignmentSubmission

TID = uuid4()
VIDEO_LESSON_ID = str(uuid4())
QUIZ_LESSON_ID = str(uuid4())


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        m.__table__ for m in (Enterprise, Training, TrainingEnrolment, TrainingLessonAttendance, TrainingProgress, TrainingAssessmentSubmission, TrainingAssignmentSubmission)
    ])
    sessions = sessionmaker(bind=engine)
    tenant_id = uuid4()
    admin = {"id": str(uuid4()), "role": "admin", "email": "admin@example.com", "name": "Admin One", "tenant_id": str(tenant_id)}
    sections = [{
        "id": "section-1", "type": "section", "title": "Session 1",
        "lessons": [
            {"id": VIDEO_LESSON_ID, "type": "video", "title": "Intro video"},
            {"id": QUIZ_LESSON_ID, "type": "quiz", "title": "Checkpoint quiz"},
        ],
    }]
    enrol_ids = {}
    with sessions() as db:
        db.add(Training(
            id=TID, enterprise_id=uuid4(), tenant_id=tenant_id, title="Course", category="Wellness",
            status="published", delivery_mode="online", sections=sections, assessments=[], assignments=[],
        ))
        e1 = TrainingEnrolment(training_id=TID, participant_name="Alice", participant_email="alice@example.com", status="enrolled", qr_code="QR-ALICE")
        e2 = TrainingEnrolment(training_id=TID, participant_name="Bob", participant_email="bob@example.com", status="active", qr_code="QR-BOB")
        e3 = TrainingEnrolment(training_id=TID, participant_name="Cancelled Carl", participant_email="carl@example.com", status="cancelled", qr_code="QR-CARL")
        db.add_all([e1, e2, e3])
        db.commit()
        enrol_ids["alice"] = str(e1.id)
        enrol_ids["bob"] = str(e2.id)
        enrol_ids["carl"] = str(e3.id)

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1/trainings")

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: admin
    with TestClient(app) as client:
        yield sessions, client, admin, enrol_ids
    engine.dispose()


def test_get_roster_lists_active_enrolments_as_not_marked(setup):
    sessions, client, admin, enrol_ids = setup
    resp = client.get(f"/api/v1/trainings/{TID}/lessons/{VIDEO_LESSON_ID}/attendance/roster")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["lesson_id"] == VIDEO_LESSON_ID
    assert body["lesson_type"] == "video"
    emails = {p["participant_email"] for p in body["participants"]}
    # Cancelled enrolment is not an active participant — excluded from the roster.
    assert emails == {"alice@example.com", "bob@example.com"}
    for p in body["participants"]:
        assert p["status"] == "not_marked"
        assert p["marked_by"] is None
        assert p["marked_at"] is None


def test_get_roster_works_for_any_lesson_type_not_just_live_venue(setup):
    sessions, client, admin, enrol_ids = setup
    resp = client.get(f"/api/v1/trainings/{TID}/lessons/{QUIZ_LESSON_ID}/attendance/roster")
    assert resp.status_code == 200, resp.text
    assert resp.json()["lesson_type"] == "quiz"


def test_get_roster_unknown_lesson_404s(setup):
    sessions, client, admin, enrol_ids = setup
    resp = client.get(f"/api/v1/trainings/{TID}/lessons/{uuid4()}/attendance/roster")
    assert resp.status_code == 404


def test_batch_mark_persists_status_and_marking_details(setup):
    sessions, client, admin, enrol_ids = setup
    url = f"/api/v1/trainings/{TID}/lessons/{VIDEO_LESSON_ID}/attendance/roster"
    resp = client.post(url, json={"records": [
        {"enrolment_id": enrol_ids["alice"], "status": "attended"},
        {"enrolment_id": enrol_ids["bob"], "status": "absent"},
    ]})
    assert resp.status_code == 200, resp.text
    by_id = {p["enrolment_id"]: p for p in resp.json()["participants"]}

    alice = by_id[enrol_ids["alice"]]
    assert alice["status"] == "attended"
    assert alice["marked_by"]["email"] == "admin@example.com"
    assert alice["marked_by"]["name"] == "Admin One"
    assert alice["marked_at"] is not None

    bob = by_id[enrol_ids["bob"]]
    assert bob["status"] == "absent"
    assert bob["marked_by"]["email"] == "admin@example.com"

    # A fresh GET reflects the same persisted state.
    get_resp = client.get(url)
    by_id_2 = {p["enrolment_id"]: p for p in get_resp.json()["participants"]}
    assert by_id_2[enrol_ids["alice"]]["status"] == "attended"
    assert by_id_2[enrol_ids["bob"]]["status"] == "absent"


def test_batch_mark_not_marked_clears_status_but_keeps_audit_history(setup):
    sessions, client, admin, enrol_ids = setup
    url = f"/api/v1/trainings/{TID}/lessons/{VIDEO_LESSON_ID}/attendance/roster"
    client.post(url, json={"records": [{"enrolment_id": enrol_ids["alice"], "status": "attended"}]})

    resp = client.post(url, json={"records": [{"enrolment_id": enrol_ids["alice"], "status": "not_marked"}]})
    assert resp.status_code == 200, resp.text
    alice = next(p for p in resp.json()["participants"] if p["enrolment_id"] == enrol_ids["alice"])
    assert alice["status"] == "not_marked"
    assert alice["marked_by"] is None
    assert alice["marked_at"] is None

    from uuid import UUID
    with sessions() as db:
        record = db.query(TrainingLessonAttendance).filter(
            TrainingLessonAttendance.training_id == TID,
            TrainingLessonAttendance.lesson_id == VIDEO_LESSON_ID,
            TrainingLessonAttendance.enrolment_id == UUID(enrol_ids["alice"]),
        ).one()
        assert record.status is None
        assert len(record.history) == 2
        assert record.history[0]["action"] == "marked"
        assert record.history[0]["new_status"] == "attended"
        assert record.history[1]["action"] == "cleared"
        assert record.history[1]["previous_status"] == "attended"
        assert record.history[1]["new_status"] is None
        assert record.history[1]["actor_email"] == "admin@example.com"


def test_batch_mark_rejects_enrolment_from_another_training(setup):
    sessions, client, admin, enrol_ids = setup
    url = f"/api/v1/trainings/{TID}/lessons/{VIDEO_LESSON_ID}/attendance/roster"
    foreign_id = str(uuid4())
    resp = client.post(url, json={"records": [
        {"enrolment_id": enrol_ids["alice"], "status": "attended"},
        {"enrolment_id": foreign_id, "status": "attended"},
    ]})
    assert resp.status_code == 422, resp.text

    # No partial write — Alice's record must NOT have been created either.
    with sessions() as db:
        assert db.query(TrainingLessonAttendance).count() == 0


def test_batch_mark_rejects_ineligible_cancelled_enrolment(setup):
    sessions, client, admin, enrol_ids = setup
    url = f"/api/v1/trainings/{TID}/lessons/{VIDEO_LESSON_ID}/attendance/roster"
    resp = client.post(url, json={"records": [{"enrolment_id": enrol_ids["carl"], "status": "attended"}]})
    assert resp.status_code == 422, resp.text


def test_batch_mark_rejects_duplicate_enrolment_id(setup):
    sessions, client, admin, enrol_ids = setup
    url = f"/api/v1/trainings/{TID}/lessons/{VIDEO_LESSON_ID}/attendance/roster"
    resp = client.post(url, json={"records": [
        {"enrolment_id": enrol_ids["alice"], "status": "attended"},
        {"enrolment_id": enrol_ids["alice"], "status": "absent"},
    ]})
    assert resp.status_code == 422, resp.text


def test_batch_mark_rejects_invalid_status_value(setup):
    sessions, client, admin, enrol_ids = setup
    url = f"/api/v1/trainings/{TID}/lessons/{VIDEO_LESSON_ID}/attendance/roster"
    resp = client.post(url, json={"records": [{"enrolment_id": enrol_ids["alice"], "status": "late"}]})
    assert resp.status_code == 422, resp.text


def test_batch_mark_rejects_empty_records(setup):
    sessions, client, admin, enrol_ids = setup
    url = f"/api/v1/trainings/{TID}/lessons/{VIDEO_LESSON_ID}/attendance/roster"
    resp = client.post(url, json={"records": []})
    assert resp.status_code == 422, resp.text


def test_batch_mark_unknown_lesson_404s(setup):
    sessions, client, admin, enrol_ids = setup
    url = f"/api/v1/trainings/{TID}/lessons/{uuid4()}/attendance/roster"
    resp = client.post(url, json={"records": [{"enrolment_id": enrol_ids["alice"], "status": "attended"}]})
    assert resp.status_code == 404


def test_roster_endpoints_reject_non_staff_role(setup):
    sessions, client, admin, enrol_ids = setup
    learner = {"id": str(uuid4()), "role": "customer", "email": "learner@example.com"}
    client.app.dependency_overrides[get_current_user] = lambda: learner
    get_resp = client.get(f"/api/v1/trainings/{TID}/lessons/{VIDEO_LESSON_ID}/attendance/roster")
    assert get_resp.status_code == 403
    post_resp = client.post(
        f"/api/v1/trainings/{TID}/lessons/{VIDEO_LESSON_ID}/attendance/roster",
        json={"records": [{"enrolment_id": enrol_ids["alice"], "status": "attended"}]},
    )
    assert post_resp.status_code == 403
    client.app.dependency_overrides[get_current_user] = lambda: admin


# --- QR check-in: POST /{training_id}/lessons/{lesson_id}/attendance/scan ---
# Marks the SAME TrainingLessonAttendance roster row the manual batch-mark
# endpoint writes — a scan and a manual tick are indistinguishable in storage.

def test_qr_scan_marks_attended_and_returns_marking_details(setup):
    sessions, client, admin, enrol_ids = setup
    url = f"/api/v1/trainings/{TID}/lessons/{VIDEO_LESSON_ID}/attendance/scan"
    resp = client.post(url, json={"qr_code": "QR-ALICE"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["result"] == "marked"
    assert body["status"] == "attended"
    assert body["enrolment_id"] == enrol_ids["alice"]
    assert body["participant_email"] == "alice@example.com"
    assert body["marked_by"]["email"] == "admin@example.com"
    assert body["marked_by"]["name"] == "Admin One"
    assert body["marked_at"] is not None

    # Reflected on the roster too.
    roster = client.get(f"/api/v1/trainings/{TID}/lessons/{VIDEO_LESSON_ID}/attendance/roster").json()
    alice = next(p for p in roster["participants"] if p["enrolment_id"] == enrol_ids["alice"])
    assert alice["status"] == "attended"


def test_qr_scan_is_idempotent_on_repeat_scan(setup):
    sessions, client, admin, enrol_ids = setup
    url = f"/api/v1/trainings/{TID}/lessons/{VIDEO_LESSON_ID}/attendance/scan"
    first = client.post(url, json={"qr_code": "QR-ALICE"})
    assert first.status_code == 200
    first_marked_at = first.json()["marked_at"]

    second = client.post(url, json={"qr_code": "QR-ALICE"})
    assert second.status_code == 200, second.text
    assert second.json()["result"] == "already_attended"
    assert second.json()["marked_at"] == first_marked_at  # unchanged — first scan wins

    with sessions() as db:
        # Exactly one row — no duplicate attendance record from the repeat scan.
        assert db.query(TrainingLessonAttendance).filter(
            TrainingLessonAttendance.training_id == TID,
            TrainingLessonAttendance.lesson_id == VIDEO_LESSON_ID,
            TrainingLessonAttendance.enrolment_id == UUID(enrol_ids["alice"]),
        ).count() == 1


def test_qr_scan_records_who_scanned_and_when_in_history(setup):
    sessions, client, admin, enrol_ids = setup
    url = f"/api/v1/trainings/{TID}/lessons/{VIDEO_LESSON_ID}/attendance/scan"
    client.post(url, json={"qr_code": "QR-ALICE"})

    with sessions() as db:
        record = db.query(TrainingLessonAttendance).filter(
            TrainingLessonAttendance.training_id == TID,
            TrainingLessonAttendance.lesson_id == VIDEO_LESSON_ID,
            TrainingLessonAttendance.enrolment_id == UUID(enrol_ids["alice"]),
        ).one()
        assert record.marked_by_email == "admin@example.com"
        assert record.history[-1]["via"] == "qr_scan"
        assert record.history[-1]["actor_email"] == "admin@example.com"
        assert record.history[-1]["new_status"] == "attended"


def test_qr_scan_rejects_unknown_qr_code(setup):
    sessions, client, admin, enrol_ids = setup
    url = f"/api/v1/trainings/{TID}/lessons/{VIDEO_LESSON_ID}/attendance/scan"
    resp = client.post(url, json={"qr_code": "QR-DOES-NOT-EXIST"})
    assert resp.status_code == 404


def test_qr_scan_rejects_cancelled_enrolment(setup):
    sessions, client, admin, enrol_ids = setup
    url = f"/api/v1/trainings/{TID}/lessons/{VIDEO_LESSON_ID}/attendance/scan"
    resp = client.post(url, json={"qr_code": "QR-CARL"})
    assert resp.status_code == 410, resp.text


def test_qr_scan_rejects_qr_from_another_training(setup):
    sessions, client, admin, enrol_ids = setup
    other_tid = uuid4()
    with sessions() as db:
        # Same tenant as `admin` (so the auth layer allows the call, and the
        # lesson id exists there too) — the point is the SERVICE rejecting a QR
        # that belongs to a different training's enrolment, not a lesson-lookup
        # or tenant-ownership 404/403 for an unrelated reason.
        db.add(Training(
            id=other_tid, enterprise_id=uuid4(), tenant_id=UUID(admin["tenant_id"]), title="Other", category="General",
            status="published", delivery_mode="online",
            sections=[{"id": "s1", "type": "section", "title": "S1", "lessons": [{"id": VIDEO_LESSON_ID, "type": "video", "title": "Intro video"}]}],
            assessments=[], assignments=[],
        ))
        db.commit()
    url = f"/api/v1/trainings/{other_tid}/lessons/{VIDEO_LESSON_ID}/attendance/scan"
    resp = client.post(url, json={"qr_code": "QR-ALICE"})
    assert resp.status_code == 404


def test_qr_scan_unknown_lesson_404s(setup):
    sessions, client, admin, enrol_ids = setup
    url = f"/api/v1/trainings/{TID}/lessons/{uuid4()}/attendance/scan"
    resp = client.post(url, json={"qr_code": "QR-ALICE"})
    assert resp.status_code == 404


def test_qr_scan_enforces_release_rule_check_in_window(setup):
    sessions, client, admin, enrol_ids = setup
    future_lesson_id = str(uuid4())
    with sessions() as db:
        training = db.get(Training, TID)
        training.sections = [{
            "id": "section-1", "type": "section", "title": "Session 1",
            "lessons": [{
                "id": future_lesson_id, "type": "video", "title": "Not yet released",
                "release_rule": {"mode": "date", "date": "2099-01-01T00:00:00"},
            }],
        }]
        db.commit()

    url = f"/api/v1/trainings/{TID}/lessons/{future_lesson_id}/attendance/scan"
    resp = client.post(url, json={"qr_code": "QR-ALICE"})
    assert resp.status_code == 403, resp.text
    assert "Check-in window closed" in resp.json()["detail"]


def test_qr_scan_endpoint_rejects_non_staff_role(setup):
    sessions, client, admin, enrol_ids = setup
    learner = {"id": str(uuid4()), "role": "customer", "email": "learner@example.com"}
    client.app.dependency_overrides[get_current_user] = lambda: learner
    resp = client.post(
        f"/api/v1/trainings/{TID}/lessons/{VIDEO_LESSON_ID}/attendance/scan",
        json={"qr_code": "QR-ALICE"},
    )
    assert resp.status_code == 403
    client.app.dependency_overrides[get_current_user] = lambda: admin

@pytest.mark.parametrize("endpoint", ["attendance/scan", "attendance"])
def test_lesson_qr_round_trip_and_wrong_day_rejection(setup, endpoint):
    from app.services.training_service import get_secure_training_content_service
    sessions, client, admin, enrol_ids = setup
    with sessions() as db:
        training = db.get(Training, TID)
        training.delivery_mode = "hybrid"
        training.sections = [{"id": "days", "type": "section", "lessons": [
            {"id": VIDEO_LESSON_ID, "type": "venue", "title": "Day 1"},
            {"id": QUIZ_LESSON_ID, "type": "live", "title": "Day 2"},
        ]}]
        db.commit()
    learner = {"role": "customer", "email": "alice@example.com"}
    def content():
        with sessions() as db:
            return get_secure_training_content_service(db, TID, learner)["sections"][0]["lessons"]
    lessons = content()
    codes = [lesson["qr_code"] for lesson in lessons]
    assert len(set(codes)) == 2
    assert lessons[0]["qr_image_base64"] != lessons[1]["qr_image_base64"]
    from app.services.response_mappers import _qr_image_base64
    assert all(l["qr_image_base64"] == _qr_image_base64(l["qr_code"]) for l in lessons)
    assert [l["qr_code"] for l in content()] == codes
    wrong = client.post(f"/api/v1/trainings/{TID}/lessons/{QUIZ_LESSON_ID}/{endpoint}",
                        json={"qr_code": codes[0], "participant_email": "bob@example.com"})
    assert wrong.status_code == 400, wrong.text
    assert not any(l["is_attended"] for l in content())
    url = f"/api/v1/trainings/{TID}/lessons/{VIDEO_LESSON_ID}/{endpoint}"
    response = client.post(url, json={"qr_code": codes[0]})
    assert response.status_code == 200, response.text
    updated = content()
    assert updated[0]["is_attended"] and updated[0]["attended_at"]
    assert not updated[1]["is_attended"]
    assert updated[1]["attended_at"] is None
    assert client.post(url, json={"qr_code": codes[0]}).status_code == 200
    assert content()[0]["attended_at"] == updated[0]["attended_at"]


def test_scoped_qr_keeps_enrolment_and_tenant_guards(setup):
    sessions, client, admin, enrol_ids = setup
    url = f"/api/v1/trainings/{TID}/lessons/{VIDEO_LESSON_ID}/attendance/scan"
    assert client.post(url, json={"qr_code": f"QR-CARL:{VIDEO_LESSON_ID}"}).status_code == 410
    assert client.post(url, json={"qr_code": f"unknown:{VIDEO_LESSON_ID}"}).status_code == 404
    admin["tenant_id"] = str(uuid4())
    assert client.post(url, json={"qr_code": f"QR-ALICE:{VIDEO_LESSON_ID}"}).status_code in (403, 404)
