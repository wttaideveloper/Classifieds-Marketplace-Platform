"""Reproduces the reported bug: GET /trainings/{id}/content returned null
schedule/check_in_window/scheduled_at for "venue" and "live" lessons.

Two real causes, both fixed here:
1. learning_response() in training_curriculum.py unconditionally nulled
   check_in_window for any lesson whose type wasn't "venue" — wiping it for
   every "live" lesson regardless of what the admin set.
2. There was no scheduled_at field at all: LessonCreate never declared it
   and _base_lesson_payload never emitted it, so mobile had no machine-
   readable datetime to show "when to join"/"when to check in," only the
   free-text schedule/check_in_window strings.

scheduled_at now flows through end to end and is formatted with an explicit
UTC offset — a naive stored value is interpreted as wall-clock time in
training.time_zone, same convention as Training.enrolment_start/enrolment_end
(see tests/test_training_enrolment_window_timezone.py)."""

from datetime import datetime
from unittest.mock import MagicMock
from uuid import uuid4

from app.services import training_service


def _training(**overrides):
    enterprise = MagicMock(business_short_name="Pulse Labs")
    defaults = dict(
        id=uuid4(), title="Yoga for Stress Relief", primary_image="https://img/x.jpg",
        enterprise=enterprise, instructor_name="Priya Menon", delivery_mode="hybrid",
        status="published", time_zone="Asia/Kolkata",
        sections=[
            {
                "id": "sec-1", "type": "section", "order": 1, "title": "Session 1",
                "schedule": "2026-10-05T14:00:00+05:30",
                "lessons": [
                    {
                        "id": "lv2", "type": "live", "title": "Live practice",
                        "meeting_link": "https://meet/x",
                        "check_in_window": "Opens 10 min before · closes 10 min after",
                        "scheduled_at": "2026-10-05T14:00:00",
                    },
                    {
                        "id": "lv3", "type": "venue", "title": "Studio floor",
                        "venue": "Studio B", "address": "214 Oak St", "pass_code": "CODE1",
                        "check_in_window": "9:00-9:20am",
                        "scheduled_at": "2026-10-06T09:00:00+05:30",
                    },
                ],
            },
        ],
        assessments=[],
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


def _mock_db(*, enrol=True, progress_lessons=None):
    from app.models.training_model import TrainingAssessmentSubmission, TrainingEnrolment, TrainingProgress

    enrol_row = MagicMock(qr_code="96FFF6F6-A9D", status="enrolled", created_at=datetime(2026, 1, 1)) if enrol else None
    prog_row = MagicMock(lessons_completed=progress_lessons or []) if progress_lessons is not None else None

    db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        name = model.__name__
        if name == "TrainingEnrolment":
            q.filter.return_value.first.return_value = enrol_row
        elif name == "TrainingProgress":
            q.filter.return_value.first.return_value = prog_row
        elif name == "TrainingAssessmentSubmission":
            q.filter.return_value.order_by.return_value.all.return_value = []
        return q

    db.query.side_effect = query_side_effect
    return db


def _get_content(training, monkeypatch, **db_kwargs):
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    db = _mock_db(**db_kwargs)
    result = training_service.get_secure_training_content_service(db, training.id, {"email": "x@example.com", "role": "learner"})
    return {l["id"]: l for l in result["sections"][0]["lessons"]}


# --- the check_in_window bug: must survive for "live", not just "venue" ---

def test_live_lesson_check_in_window_is_not_nulled(monkeypatch):
    training = _training()
    lessons = _get_content(training, monkeypatch, progress_lessons=[])
    assert lessons["lv2"]["check_in_window"] == "Opens 10 min before · closes 10 min after"


def test_venue_lesson_check_in_window_still_works(monkeypatch):
    training = _training()
    lessons = _get_content(training, monkeypatch, progress_lessons=[])
    assert lessons["lv3"]["check_in_window"] == "9:00-9:20am"


def test_live_check_in_window_nulled_when_training_not_online_capable(monkeypatch):
    training = _training(delivery_mode="physical")  # venue-only, no online_mode
    lessons = _get_content(training, monkeypatch, progress_lessons=[])
    assert lessons["lv2"]["check_in_window"] is None


# --- scheduled_at: new field, must appear with an explicit UTC offset ---

def test_scheduled_at_gets_explicit_offset_for_naive_stored_value(monkeypatch):
    # lv2's stored scheduled_at "2026-10-05T14:00:00" is naive — interpreted
    # as training.time_zone (Asia/Kolkata) wall-clock time.
    training = _training()
    lessons = _get_content(training, monkeypatch, progress_lessons=[])
    assert lessons["lv2"]["scheduled_at"] == "2026-10-05T14:00:00+05:30"


def test_scheduled_at_preserves_already_explicit_offset(monkeypatch):
    training = _training()
    lessons = _get_content(training, monkeypatch, progress_lessons=[])
    assert lessons["lv3"]["scheduled_at"] == "2026-10-06T09:00:00+05:30"


def test_scheduled_at_null_when_never_set(monkeypatch):
    training = _training(sections=[
        {"id": "sec-1", "type": "section", "order": 1, "title": "S1",
         "lessons": [{"id": "lv9", "type": "live", "title": "No schedule yet"}]},
    ])
    lessons = _get_content(training, monkeypatch, progress_lessons=[])
    assert lessons["lv9"]["scheduled_at"] is None


def test_scheduled_at_nulled_for_recorded_delivery_mode(monkeypatch):
    training = _training(delivery_mode="recorded")
    lessons = _get_content(training, monkeypatch, progress_lessons=[])
    assert lessons["lv2"]["scheduled_at"] is None


def test_scheduled_at_falls_back_to_utc_for_invalid_training_timezone(monkeypatch):
    training = _training(time_zone="Not/AZone")
    lessons = _get_content(training, monkeypatch, progress_lessons=[])
    assert lessons["lv2"]["scheduled_at"] == "2026-10-05T14:00:00+00:00"
