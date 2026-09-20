"""GET /trainings/{id}/content — restructured response: top-level card
fields, per-section unlock sequencing, per-lesson lock state (shown, not
hidden), and embedded exam/assessment data with grading keys stripped for
learners."""

from datetime import datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services import training_service


VIDEO_LESSON = {"id": "lv1", "type": "video", "title": "Intro", "duration": 10, "content_url": "https://cdn/v.mp4"}
LIVE_LESSON = {"id": "lv2", "type": "live", "title": "Live practice", "meeting_link": "https://meet/x", "join_meta": "Opens 10 min before"}
VENUE_LESSON = {"id": "lv3", "type": "venue", "title": "Studio floor", "venue": "Studio B", "address": "214 Oak St", "pass_code": "CODE1", "check_in_window": "9-9:20am"}
EXAM_LESSON = {"id": "lv4", "type": "exam", "title": "Checkpoint", "assessment_id": "assess-1"}

ASSESSMENT = {
    "id": "assess-1",
    "type": "quiz",
    "title": "Breath Awareness Quiz",
    "pass_percent": 67,
    "questions": [
        {
            "id": "q1",
            "question_text": "Which muscle is most engaged?",
            "question_type": "multiple_choice",
            "points": 1,
            "options": ["Biceps", "Diaphragm", "Hamstrings", "Trapezius"],
            "correct_answer": "b",
            "explanation": "The diaphragm does the work.",
        }
    ],
}


def _training(**overrides):
    enterprise = MagicMock(business_short_name="Pulse Labs")
    defaults = dict(
        id=uuid4(), title="Yoga for Stress Relief", primary_image="https://img/x.jpg",
        enterprise=enterprise, instructor_name="Priya Menon", delivery_mode="hybrid",
        status="published",
        sections=[
            {
                "id": "sec-1", "type": "section", "order": 1, "title": "Session 1",
                "schedule": "2026-09-16T07:00:00+05:30",
                "lessons": [VIDEO_LESSON, LIVE_LESSON, VENUE_LESSON, EXAM_LESSON],
            },
            {
                "id": "sec-2", "type": "section", "order": 2, "title": "Session 2",
                "lessons": [{"id": "lv5", "type": "video", "title": "Next up"}],
            },
        ],
        assessments=[ASSESSMENT],
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


def _mock_db(*, enrol=True, progress_lessons=None, submissions=None):
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
            q.filter.return_value.order_by.return_value.all.return_value = submissions or []
        return q

    db.query.side_effect = query_side_effect
    return db


# --- access gating (unchanged behavior) ---

def test_non_enrolled_non_staff_is_rejected(monkeypatch):
    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    db = _mock_db(enrol=False)

    with pytest.raises(HTTPException) as exc:
        training_service.get_secure_training_content_service(db, training.id, {"email": "x@example.com", "role": "learner"})
    assert exc.value.status_code == 403


def test_staff_bypasses_enrolment_requirement(monkeypatch):
    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    db = _mock_db(enrol=False)

    result = training_service.get_secure_training_content_service(db, training.id, {"email": None, "role": "admin"})
    assert result["title"] == "Yoga for Stress Relief"


# --- top-level card fields ---

def test_top_level_fields_present(monkeypatch):
    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    db = _mock_db(progress_lessons=["lv1"])

    result = training_service.get_secure_training_content_service(db, training.id, {"email": "x@example.com", "role": "learner"})

    assert result["training_id"] == str(training.id)
    assert result["title"] == "Yoga for Stress Relief"
    assert result["primary_image"] == "https://img/x.jpg"
    assert result["enterprise_name"] == "Pulse Labs"
    assert result["instructor_name"] == "Priya Menon"
    assert result["delivery_mode"] == "hybrid"
    assert result["qr_code"] == "96FFF6F6-A9D"
    assert result["total_lessons"] == 5  # 4 in section 1 + 1 in section 2
    assert result["completed_lessons"] == 1
    assert result["progress_percent"] == 20.0


# --- section unlocking ---

def test_second_section_locked_until_first_section_and_its_exam_done(monkeypatch):
    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    db = _mock_db(progress_lessons=[])  # nothing done yet

    result = training_service.get_secure_training_content_service(db, training.id, {"email": "x@example.com", "role": "learner"})

    assert result["sections"][0]["is_unlocked"] is True
    assert result["sections"][1]["is_unlocked"] is False
    assert result["sections"][1]["unlock_hint"] == "Complete Session 1 quiz to unlock"


def test_second_section_unlocks_once_content_done_and_quiz_passed(monkeypatch):
    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    passed_submission = MagicMock(assessment_id="assess-1", passed=True, score=100, submitted_at=datetime(2026, 1, 2))
    db = _mock_db(progress_lessons=["lv1", "lv2", "lv3"], submissions=[passed_submission])

    result = training_service.get_secure_training_content_service(db, training.id, {"email": "x@example.com", "role": "learner"})
    assert result["sections"][1]["is_unlocked"] is True
    assert result["sections"][1]["unlock_hint"] is None


def test_second_section_stays_locked_if_content_done_but_quiz_not_passed(monkeypatch):
    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    failed_submission = MagicMock(assessment_id="assess-1", passed=False, score=40, submitted_at=datetime(2026, 1, 2))
    db = _mock_db(progress_lessons=["lv1", "lv2", "lv3"], submissions=[failed_submission])

    result = training_service.get_secure_training_content_service(db, training.id, {"email": "x@example.com", "role": "learner"})
    assert result["sections"][1]["is_unlocked"] is False


def test_staff_sees_all_sections_unlocked(monkeypatch):
    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    db = _mock_db(enrol=False, progress_lessons=[])

    result = training_service.get_secure_training_content_service(db, training.id, {"email": None, "role": "provider"})
    assert result["sections"][1]["is_unlocked"] is True


# --- lessons shown, not hidden, with is_locked ---

def test_locked_lessons_are_shown_not_hidden(monkeypatch):
    """Section 2's single lesson must still appear in the response, marked
    locked — the old behavior hid it entirely."""
    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    db = _mock_db(progress_lessons=[])

    result = training_service.get_secure_training_content_service(db, training.id, {"email": "x@example.com", "role": "learner"})
    sec2_lessons = result["sections"][1]["lessons"]
    assert len(sec2_lessons) == 1
    assert sec2_lessons[0]["is_locked"] is True
    assert sec2_lessons[0]["content_url"] is None  # withheld while locked


def test_draft_lesson_without_preview_is_still_hidden_entirely(monkeypatch):
    training = _training(sections=[
        {"id": "sec-1", "lessons": [VIDEO_LESSON, {"id": "draft1", "type": "video", "title": "Draft", "is_draft": True, "is_preview": False}]},
    ])
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    db = _mock_db(progress_lessons=[])

    result = training_service.get_secure_training_content_service(db, training.id, {"email": "x@example.com", "role": "learner"})
    ids = [l["id"] for l in result["sections"][0]["lessons"]]
    assert "draft1" not in ids


# --- exam lesson / assessment embedding ---

def test_unlocked_exam_lesson_embeds_sanitized_assessment(monkeypatch):
    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    db = _mock_db(progress_lessons=["lv1", "lv2", "lv3"])  # section 1 content done -> exam unlocked

    result = training_service.get_secure_training_content_service(db, training.id, {"email": "x@example.com", "role": "learner"})
    exam_lesson = next(l for l in result["sections"][0]["lessons"] if l["type"] == "exam")

    assert exam_lesson["is_locked"] is False
    assert exam_lesson["assessment"]["id"] == "assess-1"
    assert exam_lesson["assessment"]["title"] == "Breath Awareness Quiz"
    assert exam_lesson["assessment"]["pass_percent"] == 67
    assert exam_lesson["assessment"]["is_submitted"] is False

    q = exam_lesson["assessment"]["questions"][0]
    assert "correct_answer" not in q
    assert q["options"] == [
        {"id": "a", "label": "Biceps"},
        {"id": "b", "label": "Diaphragm"},
        {"id": "c", "label": "Hamstrings"},
        {"id": "d", "label": "Trapezius"},
    ]
    assert q["explanation"] == "The diaphragm does the work."  # kept, not a grading key


def test_exam_lesson_locked_until_content_lessons_done(monkeypatch):
    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    db = _mock_db(progress_lessons=["lv1"])  # only 1 of 3 content lessons done

    result = training_service.get_secure_training_content_service(db, training.id, {"email": "x@example.com", "role": "learner"})
    exam_lesson = next(l for l in result["sections"][0]["lessons"] if l["type"] == "exam")
    assert exam_lesson["is_locked"] is True
    assert exam_lesson["assessment"] is None
    assert exam_lesson["detail"] == "Unlocks after content lessons"


def test_staff_sees_correct_answer_in_exam_questions(monkeypatch):
    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    db = _mock_db(enrol=False, progress_lessons=[])

    result = training_service.get_secure_training_content_service(db, training.id, {"email": None, "role": "admin"})
    exam_lesson = next(l for l in result["sections"][0]["lessons"] if l["type"] == "exam")
    q = exam_lesson["assessment"]["questions"][0]
    assert q["correct_answer"] == "b"


def test_submitted_exam_reports_score_and_passed(monkeypatch):
    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    submission = MagicMock(assessment_id="assess-1", passed=True, score=85, submitted_at=datetime(2026, 1, 2))
    db = _mock_db(progress_lessons=["lv1", "lv2", "lv3"], submissions=[submission])

    result = training_service.get_secure_training_content_service(db, training.id, {"email": "x@example.com", "role": "learner"})
    exam_lesson = next(l for l in result["sections"][0]["lessons"] if l["type"] == "exam")
    assert exam_lesson["is_completed"] is True
    assert exam_lesson["assessment"]["is_submitted"] is True
    assert exam_lesson["assessment"]["score_percent"] == 85
    assert exam_lesson["assessment"]["passed"] is True


# --- section summary / other lesson fields ---

def test_section_summary_counts_lessons_and_quizzes(monkeypatch):
    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    db = _mock_db(progress_lessons=[])

    result = training_service.get_secure_training_content_service(db, training.id, {"email": "x@example.com", "role": "learner"})
    assert result["sections"][0]["summary"] == "3 lessons · 1 quiz"


def test_live_and_venue_lesson_fields_pass_through(monkeypatch):
    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    db = _mock_db(progress_lessons=[])

    result = training_service.get_secure_training_content_service(db, training.id, {"email": "x@example.com", "role": "learner"})
    lessons = {l["id"]: l for l in result["sections"][0]["lessons"]}

    live = lessons["lv2"]
    assert live["meeting_link"] == "https://meet/x"
    assert live["join_meta"] == "Opens 10 min before"
    assert live["detail"] == "Online live · tap to join"

    venue = lessons["lv3"]
    assert venue["venue"] == "Studio B"
    assert venue["address"] == "214 Oak St"
    assert venue["pass_code"] == "CODE1"
    assert venue["check_in_window"] == "9-9:20am"
    assert venue["detail"] == "Show QR at venue"


# --- QR image on live sections (mobile: "qr_code" with no scannable image) ---

def test_live_section_receives_qr_image_when_venue_capable(monkeypatch):
    training = _training(sections=[
        {"id": "live-sec", "type": "live", "order": 1, "title": "Session 1", "lessons": [VIDEO_LESSON]},
    ])
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    db = _mock_db(progress_lessons=[])

    result = training_service.get_secure_training_content_service(db, training.id, {"email": "x@example.com", "role": "learner"})
    section = result["sections"][0]

    assert section["type"] == "live"
    assert section["qr_code"] == "96FFF6F6-A9D"
    assert section["qr_image_base64"].startswith("data:image/png;base64,")
    # Existing live-section fields (Phase-prior attendance fix) remain intact alongside the new QR fields.
    assert section["is_attended"] is False
    assert section["attended_at"] is None
    assert section["title"] == "Session 1"
    assert section["id"] == "live-sec"


def test_live_section_qr_image_encodes_the_same_qr_code(monkeypatch):
    training = _training(sections=[
        {"id": "live-sec", "type": "live", "order": 1, "title": "Session 1", "lessons": [VIDEO_LESSON]},
    ])
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    db = _mock_db(progress_lessons=[])

    result = training_service.get_secure_training_content_service(db, training.id, {"email": "x@example.com", "role": "learner"})
    section = result["sections"][0]

    # qrcode.make() is deterministic for a given payload — re-encoding the exact same
    # qr_code value through the same helper must byte-for-byte match what /content returned,
    # proving the image encodes the SAME identifier the existing admin scan flow expects.
    assert section["qr_image_base64"] == training_service._qr_image_base64(section["qr_code"])


def test_live_section_has_no_qr_when_training_is_online_only(monkeypatch):
    training = _training(delivery_mode="online", sections=[
        {"id": "live-sec", "type": "live", "order": 1, "title": "Session 1", "lessons": [VIDEO_LESSON]},
    ])
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    db = _mock_db(progress_lessons=[])

    result = training_service.get_secure_training_content_service(db, training.id, {"email": "x@example.com", "role": "learner"})
    section = result["sections"][0]

    assert section["qr_code"] is None
    assert section["qr_image_base64"] is None


def test_non_live_section_has_no_qr_fields(monkeypatch):
    training = _training()  # default fixture sections are type "section", not "live"
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    db = _mock_db(progress_lessons=[])

    result = training_service.get_secure_training_content_service(db, training.id, {"email": "x@example.com", "role": "learner"})
    section = result["sections"][0]

    assert section["type"] == "section"
    assert "qr_code" not in section
    assert "qr_image_base64" not in section


def test_content_response_backward_compatible_fields_unchanged(monkeypatch):
    """Adding qr_image_base64 must not remove or rename any existing field."""
    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    db = _mock_db(progress_lessons=["lv1"])

    result = training_service.get_secure_training_content_service(db, training.id, {"email": "x@example.com", "role": "learner"})

    for key in ("training_id", "title", "primary_image", "enterprise_name", "instructor_name",
                "delivery_mode", "progress_percent", "completed_lessons", "total_lessons",
                "qr_code", "sections"):
        assert key in result
    section = result["sections"][0]
    for key in ("id", "type", "order", "title", "summary", "schedule", "is_unlocked", "unlock_hint", "lessons"):
        assert key in section
