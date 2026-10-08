"""Marking a learner "attended" on a lesson completes that lesson for them.

Both ways of marking attendance (the manual roster and the QR scan) complete the lesson and update the learner's
progress totals. Only "attended" does. Reversing it undoes only the completion that attendance created.
"""
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
from app.models.training_model import (
    Training, TrainingAssessmentSubmission, TrainingAssignmentSubmission, TrainingEnrolment, TrainingLessonAttendance,
    TrainingProgress,
)
from app.services import training_service

TID = uuid4()
MANDATORY = str(uuid4())   # the only mandatory lesson: completing it completes the course
VENUE = str(uuid4())
VIDEO = str(uuid4())
QUIZ = str(uuid4())
TOTAL = 4


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    monkeypatch.setattr("app.services.training_notifications.notify_certificate_ready", lambda *a, **k: None)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        m.__table__ for m in (
            Enterprise, Training, TrainingEnrolment, TrainingLessonAttendance, TrainingProgress,
            TrainingAssessmentSubmission, TrainingAssignmentSubmission,
        )
    ])
    sessions = sessionmaker(bind=engine)
    tenant_id = uuid4()
    admin = {"id": str(uuid4()), "role": "admin", "email": "admin@example.com", "name": "Admin One", "tenant_id": str(tenant_id)}
    sections = [{
        "id": "section-1", "type": "section", "title": "Session 1",
        "lessons": [
            {"id": MANDATORY, "type": "video", "title": "Required lesson", "is_mandatory": True},
            {"id": VENUE, "type": "venue", "title": "Day 1 at the venue"},
            {"id": VIDEO, "type": "video", "title": "Optional video"},
            {"id": QUIZ, "type": "quiz", "title": "Checkpoint quiz"},
        ],
    }]
    enrol_ids = {}
    with sessions() as db:
        db.add(Training(
            id=TID, enterprise_id=uuid4(), tenant_id=tenant_id, title="Course", category="Wellness",
            status="published", delivery_mode="online", sections=sections, assessments=[], assignments=[],
        ))
        for key, name, status in (("alice", "Alice", "enrolled"), ("bob", "Bob", "active"), ("carl", "Cancelled Carl", "cancelled")):
            e = TrainingEnrolment(
                training_id=TID, participant_name=name, participant_email=f"{key}@example.com",
                status=status, qr_code=f"QR-{key.upper()}",
            )
            db.add(e)
            db.commit()
            enrol_ids[key] = str(e.id)

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1/trainings")

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: admin
    with TestClient(app) as client:
        yield sessions, client, enrol_ids
    engine.dispose()


def roster_url(lesson):
    return f"/api/v1/trainings/{TID}/lessons/{lesson}/attendance/roster"


def scan_url(lesson):
    return f"/api/v1/trainings/{TID}/lessons/{lesson}/attendance/scan"


def mark(client, enrol_ids, lesson, **statuses):
    records = [{"enrolment_id": enrol_ids[who], "status": status} for who, status in statuses.items()]
    resp = client.post(roster_url(lesson), json={"records": records})
    assert resp.status_code == 200, resp.text
    return {p["participant_email"].split("@")[0]: p for p in resp.json()["participants"]}


def progress(sessions, who="alice"):
    with sessions() as db:
        return db.query(TrainingProgress).filter_by(training_id=TID, participant_email=f"{who}@example.com").first()


def completed(sessions, who="alice"):
    prog = progress(sessions, who)
    return set(prog.lessons_completed or []) if prog else set()


# --- attended completes the lesson ---------------------------------------------------------------------------

def test_roster_attended_completes_the_lesson_and_updates_the_totals(setup):
    sessions, client, enrol_ids = setup

    people = mark(client, enrol_ids, VIDEO, alice="attended")

    assert completed(sessions) == {VIDEO}
    alice = people["alice"]
    assert alice["status"] == "attended" and alice["is_completed"] is True and alice["completed_by_attendance"] is True
    assert alice["progress"]["lessons_done"] == 1 and alice["progress"]["total_lessons"] == TOTAL
    assert alice["progress"]["overall_percent"] == 25.0
    assert float(progress(sessions).overall_percent) == 25.0


def test_qr_scan_attended_completes_the_lesson_and_updates_the_totals(setup):
    sessions, client, enrol_ids = setup

    resp = client.post(scan_url(VENUE), json={"qr_code": "QR-ALICE"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["result"] == "marked" and body["status"] == "attended"
    assert body["is_completed"] is True and body["completed_by_attendance"] is True
    assert body["progress"]["lessons_done"] == 1 and body["progress"]["overall_percent"] == 25.0
    assert completed(sessions) == {VENUE}


def test_a_lesson_specific_qr_also_completes_it(setup):
    sessions, client, enrol_ids = setup

    resp = client.post(scan_url(VENUE), json={"qr_code": f"QR-ALICE:{VENUE}"})

    assert resp.status_code == 200 and resp.json()["is_completed"] is True
    assert completed(sessions) == {VENUE}


def test_only_the_marked_learner_is_completed(setup):
    sessions, client, enrol_ids = setup

    people = mark(client, enrol_ids, VIDEO, alice="attended", bob="absent")

    assert completed(sessions, "alice") == {VIDEO}
    assert progress(sessions, "bob") is None
    assert people["bob"]["is_completed"] is False and people["bob"]["completed_by_attendance"] is False
    assert people["bob"]["progress"]["lessons_done"] == 0


@pytest.mark.parametrize("status", ["absent", "not_marked"])
def test_absent_and_not_marked_do_not_complete_anything(setup, status):
    sessions, client, enrol_ids = setup

    people = mark(client, enrol_ids, VIDEO, alice=status)

    assert progress(sessions) is None
    assert people["alice"]["is_completed"] is False and people["alice"]["progress"]["lessons_done"] == 0


def test_the_roster_get_shows_completion_for_everyone(setup):
    sessions, client, enrol_ids = setup
    mark(client, enrol_ids, VIDEO, alice="attended")

    resp = client.get(roster_url(VIDEO))

    people = {p["participant_email"].split("@")[0]: p for p in resp.json()["participants"]}
    assert people["alice"]["is_completed"] is True and people["bob"]["is_completed"] is False


def test_a_quiz_is_not_completed_by_attendance(setup):
    """Quizzes complete by submitting the assessment (that is what /content reads); a tick must not pass them."""
    sessions, client, enrol_ids = setup

    people = mark(client, enrol_ids, QUIZ, alice="attended")

    assert people["alice"]["status"] == "attended"
    assert people["alice"]["is_completed"] is False
    assert progress(sessions) is None


def test_completing_the_course_by_attendance_issues_the_completion(setup):
    sessions, client, enrol_ids = setup

    people = mark(client, enrol_ids, MANDATORY, alice="attended")

    prog = progress(sessions)
    assert prog.completed_at is not None and prog.certificate_url
    assert people["alice"]["progress"]["mandatory_done"] == 1 and people["alice"]["progress"]["completed_at"] is not None


# --- idempotent ----------------------------------------------------------------------------------------------

def test_repeating_attended_on_the_roster_changes_nothing(setup):
    sessions, client, enrol_ids = setup
    mark(client, enrol_ids, MANDATORY, alice="attended")
    first = progress(sessions)
    snapshot = (list(first.lessons_completed), list(first.attendance_completed_lessons), first.completed_at, first.overall_percent)

    for _ in range(3):
        people = mark(client, enrol_ids, MANDATORY, alice="attended")

    again = progress(sessions)
    assert (list(again.lessons_completed), list(again.attendance_completed_lessons), again.completed_at, again.overall_percent) == snapshot
    assert people["alice"]["is_completed"] is True and people["alice"]["progress"]["lessons_done"] == 1


def test_scanning_twice_is_idempotent(setup):
    sessions, client, enrol_ids = setup
    client.post(scan_url(VENUE), json={"qr_code": "QR-ALICE"})
    before = progress(sessions)
    snapshot = (list(before.lessons_completed), list(before.attendance_completed_lessons), before.overall_percent)

    second = client.post(scan_url(VENUE), json={"qr_code": "QR-ALICE"})

    assert second.status_code == 200 and second.json()["result"] == "already_attended"
    assert second.json()["is_completed"] is True
    after = progress(sessions)
    assert (list(after.lessons_completed), list(after.attendance_completed_lessons), after.overall_percent) == snapshot


def test_a_rescan_completes_an_attendance_recorded_before_this_feature(setup):
    sessions, client, enrol_ids = setup
    with sessions() as db:
        db.add(TrainingLessonAttendance(
            training_id=TID, lesson_id=VENUE, enrolment_id=UUID(enrol_ids["alice"]), status="attended", history=[],
        ))
        db.commit()
    assert progress(sessions) is None

    resp = client.post(scan_url(VENUE), json={"qr_code": "QR-ALICE"})

    assert resp.json()["result"] == "already_attended" and resp.json()["is_completed"] is True
    assert completed(sessions) == {VENUE}


# --- reversing attendance ------------------------------------------------------------------------------------

@pytest.mark.parametrize("new_status", ["absent", "not_marked"])
def test_reversing_attendance_undoes_the_completion_it_created(setup, new_status):
    sessions, client, enrol_ids = setup
    mark(client, enrol_ids, VIDEO, alice="attended")

    people = mark(client, enrol_ids, VIDEO, alice=new_status)

    assert completed(sessions) == set()
    assert list(progress(sessions).attendance_completed_lessons) == []
    assert float(progress(sessions).overall_percent) == 0.0
    assert people["alice"]["is_completed"] is False and people["alice"]["progress"]["lessons_done"] == 0


def test_reversing_keeps_other_lessons_and_recomputes_the_totals(setup):
    sessions, client, enrol_ids = setup
    mark(client, enrol_ids, VIDEO, alice="attended")
    mark(client, enrol_ids, VENUE, alice="attended")

    people = mark(client, enrol_ids, VIDEO, alice="absent")

    assert completed(sessions) == {VENUE}
    assert people["alice"]["progress"]["lessons_done"] == 1 and people["alice"]["progress"]["overall_percent"] == 25.0


def test_reversing_withdraws_a_completion_that_only_attendance_earned(setup):
    sessions, client, enrol_ids = setup
    mark(client, enrol_ids, MANDATORY, alice="attended")
    assert progress(sessions).certificate_url

    mark(client, enrol_ids, MANDATORY, alice="not_marked")

    prog = progress(sessions)
    assert prog.completed_at is None and prog.certificate_url is None


def test_attended_again_after_a_reversal_completes_it_again(setup):
    sessions, client, enrol_ids = setup
    mark(client, enrol_ids, VIDEO, alice="attended")
    mark(client, enrol_ids, VIDEO, alice="absent")

    mark(client, enrol_ids, VIDEO, alice="attended")

    assert completed(sessions) == {VIDEO}


def test_reversing_something_that_was_never_attended_is_a_no_op(setup):
    sessions, client, enrol_ids = setup
    mark(client, enrol_ids, VIDEO, alice="absent")
    mark(client, enrol_ids, VIDEO, alice="not_marked")

    assert progress(sessions) is None


# --- the learner's own completions are never undone ----------------------------------------------------------

def test_a_lesson_the_learner_completed_themselves_survives_the_reversal(setup):
    sessions, client, enrol_ids = setup
    with sessions() as db:
        training_service.complete_lesson_service(db, TID, VIDEO, "alice@example.com")

    people = mark(client, enrol_ids, VIDEO, alice="attended")
    assert people["alice"]["is_completed"] is True and people["alice"]["completed_by_attendance"] is False

    people = mark(client, enrol_ids, VIDEO, alice="absent")

    assert completed(sessions) == {VIDEO}  # still the learner's
    assert people["alice"]["is_completed"] is True and people["alice"]["completed_by_attendance"] is False


def test_a_lesson_the_learner_completes_after_attendance_becomes_theirs(setup):
    sessions, client, enrol_ids = setup
    mark(client, enrol_ids, VIDEO, alice="attended")
    assert list(progress(sessions).attendance_completed_lessons) == [VIDEO]

    with sessions() as db:
        training_service.complete_lesson_service(db, TID, VIDEO, "alice@example.com")
    assert list(progress(sessions).attendance_completed_lessons) == []

    mark(client, enrol_ids, VIDEO, alice="absent")

    assert completed(sessions) == {VIDEO}


def test_reversing_one_lesson_does_not_touch_another_the_learner_completed(setup):
    sessions, client, enrol_ids = setup
    with sessions() as db:
        training_service.complete_lesson_service(db, TID, MANDATORY, "alice@example.com")
    mark(client, enrol_ids, VIDEO, alice="attended")

    mark(client, enrol_ids, VIDEO, alice="absent")

    assert completed(sessions) == {MANDATORY}
    assert progress(sessions).completed_at is not None  # the learner's own completion still stands


# --- the older self check-in / live session path -------------------------------------------------------------

def test_lesson_self_checkin_now_updates_the_totals_too(setup):
    sessions, client, enrol_ids = setup
    with sessions() as db:
        resp = training_service.record_lesson_attendance_service(db, TID, VENUE, "alice@example.com")

    assert resp["progress_marked"] is True
    prog = progress(sessions)
    assert set(prog.lessons_completed) == {VENUE} and float(prog.overall_percent) == 25.0


def test_an_admin_scan_through_the_live_session_endpoint_updates_the_totals(setup):
    sessions, client, enrol_ids = setup

    resp = client.post(
        f"/api/v1/trainings/{TID}/lessons/{VENUE}/attendance", json={"qr_code": "QR-ALICE"}
    )

    assert resp.status_code == 200, resp.text
    prog = progress(sessions)
    assert set(prog.lessons_completed) == {VENUE} and float(prog.overall_percent) == 25.0
