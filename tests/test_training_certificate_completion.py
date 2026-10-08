"""A learner who has finished a training gets their certificate.

Report: "I completed all the sessions but did not receive the certificate", only on some trainings. /content showed
100% (2 of 2 lessons) while GET /certificate answered 404 "Certificate not yet available".

The certificate was only issued at the moment a lesson was completed through the normal completion path, and that
path counted lessons differently from /content: raw sections[].lessons only (not "items"), and it required the
percentage to be exactly 100 (so any extra id in the learner's completed list stopped it for good). A learner whose
lessons were recorded another way (an older venue/live check-in) never triggered it at all.
"""
from datetime import datetime
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

L1, L2, L3 = str(uuid4()), str(uuid4()), str(uuid4())
ASSIGNMENT = str(uuid4())
LEARNER = "alice@example.com"


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    sent = []
    monkeypatch.setattr(
        "app.services.training_notifications.notify_certificate_ready", lambda *a, **k: sent.append(k.get("email"))
    )
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        m.__table__ for m in (
            Enterprise, Training, TrainingEnrolment, TrainingLessonAttendance, TrainingProgress,
            TrainingAssessmentSubmission, TrainingAssignmentSubmission,
        )
    ])
    sessions = sessionmaker(bind=engine)
    state = {"learner": {"id": str(uuid4()), "role": "customer", "email": LEARNER}}

    def make_training(sections, *, enrol_status="enrolled", tid=None):
        tid = tid or uuid4()
        with sessions() as db:
            db.add(Training(
                id=tid, enterprise_id=uuid4(), tenant_id=uuid4(), title="venue", category="Wellness",
                status="published", delivery_mode="online", sections=sections, assessments=[], assignments=[],
            ))
            db.add(TrainingEnrolment(
                training_id=tid, participant_name="Alice", participant_email=LEARNER, status=enrol_status, qr_code=uuid4().hex,
            ))
            db.commit()
        return tid

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1/trainings")

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: state["learner"]
    with TestClient(app) as client:
        yield type("W", (), dict(sessions=sessions, client=client, make_training=staticmethod(make_training), sent=sent))
    engine.dispose()


def two_lessons(key="lessons", **flags):
    return [{"id": "s1", "type": "section", "title": "Day 1", key: [
        {"id": L1, "type": "text", "title": "One", **flags}, {"id": L2, "type": "text", "title": "Two", **flags},
    ]}]


def set_progress(world, tid, lessons, **extra):
    with world.sessions() as db:
        db.add(TrainingProgress(training_id=tid, participant_email=LEARNER, lessons_completed=lessons, overall_percent="0", **extra))
        db.commit()


def progress(world, tid):
    with world.sessions() as db:
        return db.query(TrainingProgress).filter_by(training_id=tid, participant_email=LEARNER).first()


def certificate(world, tid):
    return world.client.get(f"/api/v1/trainings/{tid}/certificate")


def complete(world, tid, lesson):
    with world.sessions() as db:
        return training_service.complete_lesson_service(db, tid, lesson, LEARNER)


# --- the reported case: finished, 100% in /content, no certificate ----------------------------------------------

def test_a_learner_who_finished_but_never_got_a_certificate_gets_it_when_they_ask(world):
    """Progress was saved without the certificate being issued (e.g. an older venue check-in)."""
    tid = world.make_training(two_lessons())
    set_progress(world, tid, [L1, L2])
    assert progress(world, tid).certificate_url is None

    resp = certificate(world, tid)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["certificate_url"].endswith(f"/trainings/{tid}/certificate.pdf?participant_email={LEARNER}")
    assert body["completed_at"] is not None and float(body["overall_percent"]) == 100.0
    assert progress(world, tid).completed_at is not None


def test_the_learner_is_told_once(world):
    tid = world.make_training(two_lessons())
    set_progress(world, tid, [L1, L2])

    certificate(world, tid)
    certificate(world, tid)

    assert world.sent == [LEARNER]


def test_the_certificate_pdf_works_after_it_is_issued_this_way(world):
    tid = world.make_training(two_lessons())
    set_progress(world, tid, [L1, L2])

    assert certificate(world, tid).status_code == 200
    pdf = world.client.get(f"/api/v1/trainings/{tid}/certificate.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_a_learner_who_has_not_finished_still_gets_404(world):
    tid = world.make_training(two_lessons())
    set_progress(world, tid, [L1])

    resp = certificate(world, tid)

    assert resp.status_code == 404
    assert progress(world, tid).certificate_url is None and world.sent == []


def test_a_learner_with_no_progress_gets_404(world):
    tid = world.make_training(two_lessons())

    assert certificate(world, tid).status_code == 404


@pytest.mark.parametrize("status", ["cancelled", "rejected"])
def test_a_learner_without_an_active_enrolment_gets_no_certificate(world, status):
    tid = world.make_training(two_lessons(), enrol_status=status)
    set_progress(world, tid, [L1, L2])

    assert certificate(world, tid).status_code == 404
    assert progress(world, tid).certificate_url is None


def test_finishing_only_the_mandatory_lessons_is_enough(world):
    sections = [{"id": "s1", "type": "section", "title": "Day 1", "lessons": [
        {"id": L1, "type": "text", "title": "Required", "is_mandatory": True},
        {"id": L2, "type": "text", "title": "Optional"},
    ]}]
    tid = world.make_training(sections)
    set_progress(world, tid, [L1])

    assert certificate(world, tid).status_code == 200


def test_a_lesson_done_by_submitting_its_assignment_counts_like_in_content(world):
    sections = [{"id": "s1", "type": "section", "title": "Day 1", "lessons": [
        {"id": L1, "type": "text", "title": "One"},
        {"id": L2, "type": "assignment", "title": "Task", "assignment_id": ASSIGNMENT},
    ]}]
    tid = world.make_training(sections)
    set_progress(world, tid, [L1])
    with world.sessions() as db:
        db.add(TrainingAssignmentSubmission(
            training_id=tid, assignment_id=ASSIGNMENT, participant_email=LEARNER, files=[], submitted_at=datetime.utcnow(),
        ))
        db.commit()

    assert certificate(world, tid).status_code == 200


# --- why completion itself missed it ------------------------------------------------------------------------------

def test_a_training_whose_lessons_are_stored_under_items_issues_the_certificate(world):
    """Completion read only sections[].lessons, so for 'items' it saw no lessons and never completed."""
    tid = world.make_training(two_lessons(key="items"))

    complete(world, tid, L1)
    assert progress(world, tid).certificate_url is None
    result = complete(world, tid, L2)

    assert result["overall_percent"] == 100.0 and result["certificate_url"]
    assert progress(world, tid).certificate_url


def test_an_extra_completed_id_no_longer_blocks_the_certificate(world):
    """A removed lesson or a whole-session id in the completed list used to push the percentage past 100,
    and the check was 'exactly 100', so the course could never count as complete."""
    tid = world.make_training(two_lessons())
    set_progress(world, tid, [str(uuid4()), "s1"])  # ids that are not lessons of this training

    complete(world, tid, L1)
    result = complete(world, tid, L2)

    assert result["overall_percent"] == 100.0 and result["lessons_done"] == 2
    assert result["certificate_url"] and progress(world, tid).completed_at is not None


def test_the_percentage_never_goes_over_100(world):
    tid = world.make_training(two_lessons())
    set_progress(world, tid, [str(uuid4()) for _ in range(5)])

    result = complete(world, tid, L1)

    assert result["overall_percent"] == 50.0


def test_completing_again_does_not_move_the_completion_date(world):
    tid = world.make_training(two_lessons())
    complete(world, tid, L1)
    complete(world, tid, L2)
    first = progress(world, tid).completed_at

    complete(world, tid, L2)

    assert progress(world, tid).completed_at == first
    assert world.sent == [LEARNER]


def test_the_last_lesson_recorded_by_a_venue_check_in_now_issues_the_certificate(world):
    """The older check-in endpoint used to add the lesson to the completed list and stop there."""
    sections = [{"id": "s1", "type": "section", "title": "Day 1", "lessons": [
        {"id": L1, "type": "text", "title": "One"},
        {"id": L3, "type": "venue", "title": "At the venue"},
    ]}]
    tid = world.make_training(sections)
    complete(world, tid, L1)

    with world.sessions() as db:
        training_service.record_lesson_attendance_service(db, tid, L3, LEARNER)

    prog = progress(world, tid)
    assert prog.certificate_url and prog.completed_at is not None
    assert certificate(world, tid).status_code == 200


# --- the repair is also available in bulk --------------------------------------------------------------------------

def test_a_dry_run_reports_without_writing(world):
    tid = world.make_training(two_lessons())
    set_progress(world, tid, [L1, L2])

    with world.sessions() as db:
        assert training_service.issue_earned_certificate(db, tid, LEARNER, dry_run=True) is True

    prog = progress(world, tid)
    assert prog.certificate_url is None and prog.completed_at is None and world.sent == []


def test_issue_earned_certificate_returns_whether_it_issued(world):
    tid = world.make_training(two_lessons())
    set_progress(world, tid, [L1, L2])

    with world.sessions() as db:
        assert training_service.issue_earned_certificate(db, tid, LEARNER, notify=False) is True
        assert training_service.issue_earned_certificate(db, tid, LEARNER, notify=False) is False  # already has one
    assert world.sent == []  # notify=False for a silent bulk repair
