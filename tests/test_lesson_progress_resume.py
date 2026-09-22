"""Lesson/video resume tracking: POST .../progress, GET .../progress, and the
resume_section_id/resume_lesson_id + per-lesson position additions to
GET .../content."""
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.models.enterprise_model import Enterprise
from app.models.training_model import Training, TrainingAssessmentSubmission, TrainingAssignmentSubmission, TrainingEnrolment, TrainingProgress
from app.schemas.training_schema import LessonProgressSaveRequest, LessonProgressSaveResponse, TrainingProgressResponse
from app.services import training_service as service


@pytest.fixture
def progress_db(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[Enterprise.__table__, Training.__table__, TrainingEnrolment.__table__, TrainingProgress.__table__, TrainingAssignmentSubmission.__table__, TrainingAssessmentSubmission.__table__])
    sessions = sessionmaker(bind=engine)
    ent_id, tid = uuid4(), uuid4()
    section_id, lesson_id, other_lesson_id = "sec-1", "lesson-1", "lesson-2"
    email = "learner@example.com"
    with sessions() as db:
        db.add(Enterprise(id=ent_id, business_short_name="Acme", business_legal_name="Acme Ltd", business_email="a@a.com"))
        db.add(Training(
            id=tid, enterprise_id=ent_id, title="Yoga", category="Wellness",
            delivery_mode="self_paced", status="published",
            sections=[{"id": section_id, "title": "Module 1", "lessons": [
                {"id": lesson_id, "title": "Intro Video", "type": "video"},
                {"id": other_lesson_id, "title": "Second Video", "type": "video"},
            ]}],
        ))
        db.commit()
        db.add(TrainingEnrolment(training_id=tid, participant_name="Learner", participant_email=email, status="enrolled"))
        db.commit()
    yield sessions, tid, section_id, lesson_id, other_lesson_id, email
    engine.dispose()


def test_save_lesson_progress_returns_documented_shape(progress_db):
    sessions, tid, section_id, lesson_id, _, email = progress_db
    with sessions() as db:
        payload = LessonProgressSaveRequest(position_seconds=1800, duration_seconds=3600, section_id=section_id)
        data = service.save_lesson_progress_service(db, tid, lesson_id, payload, email)
        resp = LessonProgressSaveResponse(message="Progress saved", data=data)

    assert resp.data.training_id == tid
    assert resp.data.section_id == section_id
    assert resp.data.lesson_id == lesson_id
    assert resp.data.position_seconds == 1800
    assert resp.data.duration_seconds == 3600
    assert resp.data.progress_percent == 50.0
    assert resp.data.is_completed is False
    assert resp.data.last_accessed_at is not None


def test_save_lesson_progress_infers_section_id_when_omitted(progress_db):
    sessions, tid, section_id, lesson_id, _, email = progress_db
    with sessions() as db:
        payload = LessonProgressSaveRequest(position_seconds=10, duration_seconds=100)
        data = service.save_lesson_progress_service(db, tid, lesson_id, payload, email)
    assert data["section_id"] == section_id


def test_save_lesson_progress_requires_active_enrolment(progress_db):
    sessions, tid, section_id, lesson_id, _, _ = progress_db
    with sessions() as db, pytest.raises(Exception) as exc:
        payload = LessonProgressSaveRequest(position_seconds=10, duration_seconds=100)
        service.save_lesson_progress_service(db, tid, lesson_id, payload, "stranger@example.com")
    assert exc.value.status_code == 403


def test_save_lesson_progress_404s_for_unknown_lesson(progress_db):
    sessions, tid, _, _, _, email = progress_db
    with sessions() as db, pytest.raises(Exception) as exc:
        payload = LessonProgressSaveRequest(position_seconds=10, duration_seconds=100)
        service.save_lesson_progress_service(db, tid, "does-not-exist", payload, email)
    assert exc.value.status_code == 404


def test_get_progress_reports_resume_and_per_lesson_positions(progress_db):
    sessions, tid, section_id, lesson_id, other_lesson_id, email = progress_db
    with sessions() as db:
        service.save_lesson_progress_service(db, tid, lesson_id, LessonProgressSaveRequest(position_seconds=1800, duration_seconds=3600, section_id=section_id), email)
    with sessions() as db:
        result = service.get_training_progress_service(db, tid, participant_email=email)
        validated = TrainingProgressResponse.model_validate(result)

    assert validated.training_id == tid
    assert validated.resume_section_id == section_id
    assert validated.resume_lesson_id == lesson_id
    assert validated.progress_percent == validated.overall_percent
    assert validated.completed_lessons == validated.lessons_done
    assert len(validated.lessons) == 1
    pos = validated.lessons[0]
    assert pos.lesson_id == lesson_id
    assert pos.position_seconds == 1800
    assert pos.duration_seconds == 3600
    assert pos.is_completed is False


def test_resume_prefers_most_recently_accessed_incomplete_lesson(progress_db):
    sessions, tid, section_id, lesson_id, other_lesson_id, email = progress_db
    with sessions() as db:
        service.save_lesson_progress_service(db, tid, lesson_id, LessonProgressSaveRequest(position_seconds=100, duration_seconds=1000, section_id=section_id), email)
    with sessions() as db:
        # second call, later, on the other lesson — this one should become resume target
        service.save_lesson_progress_service(db, tid, other_lesson_id, LessonProgressSaveRequest(position_seconds=200, duration_seconds=1000, section_id=section_id), email)
    with sessions() as db:
        result = service.get_training_progress_service(db, tid, participant_email=email)
    assert result["resume_lesson_id"] == other_lesson_id
    assert len(result["lessons"]) == 2


def test_completed_lesson_advances_resume_to_next_incomplete_lesson(progress_db):
    """Reproduces the mobile-reported bug: saving progress that completes a
    lesson (position_seconds == duration_seconds, is_completed becomes true)
    must not leave resume null when another lesson is still incomplete — it
    must advance to that next lesson in curriculum order."""
    sessions, tid, section_id, lesson_id, other_lesson_id, email = progress_db
    with sessions() as db:
        service.save_lesson_progress_service(db, tid, lesson_id, LessonProgressSaveRequest(position_seconds=100, duration_seconds=1000, section_id=section_id), email)
    with sessions() as db:
        service._apply_lesson_completion(db, tid, lesson_id, email)
    with sessions() as db:
        result = service.get_training_progress_service(db, tid, participant_email=email)
    assert result["resume_lesson_id"] == other_lesson_id
    assert result["resume_section_id"] == section_id
    assert result["lessons"][0]["is_completed"] is True


def test_resume_is_null_once_every_lesson_is_completed(progress_db):
    sessions, tid, section_id, lesson_id, other_lesson_id, email = progress_db
    with sessions() as db:
        service.save_lesson_progress_service(db, tid, lesson_id, LessonProgressSaveRequest(position_seconds=100, duration_seconds=1000, section_id=section_id), email)
        service._apply_lesson_completion(db, tid, lesson_id, email)
        service._apply_lesson_completion(db, tid, other_lesson_id, email)
    with sessions() as db:
        result = service.get_training_progress_service(db, tid, participant_email=email)
    assert result["resume_lesson_id"] is None
    assert result["resume_section_id"] is None


def test_content_endpoint_also_advances_resume_past_a_completed_lesson(progress_db):
    sessions, tid, section_id, lesson_id, other_lesson_id, email = progress_db
    with sessions() as db:
        service.save_lesson_progress_service(db, tid, lesson_id, LessonProgressSaveRequest(position_seconds=3, duration_seconds=3, section_id=section_id), email)
        service._apply_lesson_completion(db, tid, lesson_id, email)
    with sessions() as db:
        content = service.get_secure_training_content_service(db, tid, {"email": email, "role": "participant"})
    assert content["resume_lesson_id"] == other_lesson_id
    assert content["resume_section_id"] == section_id


def test_content_endpoint_includes_resume_fields_and_lesson_position(progress_db):
    sessions, tid, section_id, lesson_id, _, email = progress_db
    with sessions() as db:
        service.save_lesson_progress_service(db, tid, lesson_id, LessonProgressSaveRequest(position_seconds=1800, duration_seconds=3600, section_id=section_id), email)
    with sessions() as db:
        content = service.get_secure_training_content_service(db, tid, {"email": email, "role": "participant"})

    assert content["resume_section_id"] == section_id
    assert content["resume_lesson_id"] == lesson_id
    lesson_item = content["sections"][0]["lessons"][0]
    assert lesson_item["progress_seconds"] == 1800
    assert lesson_item["duration_seconds"] == 3600
    assert lesson_item["last_accessed_at"] is not None

    other_item = content["sections"][0]["lessons"][1]
    assert other_item["progress_seconds"] is None
