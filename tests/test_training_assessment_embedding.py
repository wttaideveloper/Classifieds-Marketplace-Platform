"""Embed the full assessment object into each section/lesson that
references one via assessment_id, for GET /trainings/{id} — so mobile can
render a quiz without a second round trip. correct_answer (and the
question-bank 'reusable' flag) must never leak into the embedded copy, and
the existing top-level assessments[] array must be left completely
untouched — including its own correct_answer values."""

from unittest.mock import MagicMock
from uuid import uuid4

from app.services.training_service import _embed_assessments_into_sections, _sanitize_assessment_for_learner, get_training_service


ASSESSMENT = {
    "id": "42aec07b-0000-0000-0000-000000000001",
    "type": "quiz",
    "title": "Gentle Mobility Routines",
    "questions": [
        {
            "id": "6a54292d-0000-0000-0000-000000000001",
            "question_text": "Task: Record a 2-min video practicing the Morning Stretch Flow.",
            "question_type": "short_answer",
            "options": None,
            "correct_answer": "some grading rubric text",
            "points": 1,
            "explanation": None,
            "reusable": True,
        }
    ],
}


def _section_with_lesson(**overrides):
    section = {
        "id": "sec-1",
        "title": "Module 1",
        "assessment_id": None,
        "lessons": [
            {
                "id": "ad182f63-0000-0000-0000-000000000001",
                "type": "text",
                "title": "Lesson 2: Desk Yoga for Professionals",
                "assessment_id": ASSESSMENT["id"],
            }
        ],
    }
    section.update(overrides)
    return section


# --- _sanitize_assessment_for_learner ---

def test_sanitize_strips_correct_answer_and_reusable():
    sanitized = _sanitize_assessment_for_learner(ASSESSMENT)
    q = sanitized["questions"][0]
    assert "correct_answer" not in q
    assert "reusable" not in q
    assert q["question_text"] == ASSESSMENT["questions"][0]["question_text"]
    assert q["points"] == 1
    assert q["explanation"] is None


def test_sanitize_does_not_mutate_the_original():
    original_copy = {"id": "x", "questions": [{"id": "q1", "correct_answer": "secret"}]}
    _sanitize_assessment_for_learner(original_copy)
    assert original_copy["questions"][0]["correct_answer"] == "secret"


# --- _embed_assessments_into_sections ---

def test_lesson_gets_matching_assessment_embedded():
    sections = [_section_with_lesson()]
    result = _embed_assessments_into_sections(sections, [ASSESSMENT])

    lesson = result[0]["lessons"][0]
    assert lesson["assessment_id"] == ASSESSMENT["id"]  # unchanged, backward compatible
    assert lesson["assessment"]["id"] == ASSESSMENT["id"]
    assert lesson["assessment"]["title"] == "Gentle Mobility Routines"
    assert "correct_answer" not in lesson["assessment"]["questions"][0]


def test_lesson_without_assessment_id_gets_null_assessment():
    sections = [{"id": "sec-1", "assessment_id": None, "lessons": [{"id": "l1", "title": "No quiz"}]}]
    result = _embed_assessments_into_sections(sections, [ASSESSMENT])
    assert result[0]["lessons"][0]["assessment"] is None


def test_lesson_with_dangling_assessment_id_gets_null_assessment():
    """assessment_id references an assessment that no longer exists (deleted) — must not 500, just null."""
    sections = [{"id": "sec-1", "lessons": [{"id": "l1", "assessment_id": "does-not-exist"}]}]
    result = _embed_assessments_into_sections(sections, [ASSESSMENT])
    assert result[0]["lessons"][0]["assessment"] is None


def test_section_level_assessment_embedded_for_symmetry():
    section_assessment = {**ASSESSMENT, "id": "section-quiz-id"}
    sections = [{"id": "sec-1", "assessment_id": "section-quiz-id", "lessons": []}]
    result = _embed_assessments_into_sections(sections, [ASSESSMENT, section_assessment])
    assert result[0]["assessment"]["id"] == "section-quiz-id"


def test_embedding_does_not_mutate_original_sections_list():
    """Critical: must never mutate the ORM-tracked sections JSONB column in
    place — that would corrupt persisted data on the next commit."""
    sections = [_section_with_lesson()]
    original_lesson = sections[0]["lessons"][0]

    _embed_assessments_into_sections(sections, [ASSESSMENT])

    assert "assessment" not in original_lesson  # source untouched
    assert original_lesson is sections[0]["lessons"][0]  # sanity: still the same dict


def test_embedding_does_not_mutate_original_assessments_list():
    assessments = [ASSESSMENT]
    sections = [_section_with_lesson()]
    _embed_assessments_into_sections(sections, assessments)
    assert "correct_answer" in assessments[0]["questions"][0]  # top-level assessments[] untouched


# --- end-to-end via get_training_service ---

def test_get_training_service_embeds_sanitized_assessment_and_keeps_top_level_assessments(monkeypatch):
    from app.services import training_service
    from tests.test_training_endpoints import _training_stub

    training = _training_stub(sections=[_section_with_lesson()], assessments=[ASSESSMENT])
    tid = training.id
    monkeypatch.setattr(training_service, "get_training_by_id", lambda db, t: training)

    db = MagicMock()
    db.query.return_value.filter.return_value.count.return_value = 0
    db.query.return_value.filter.return_value.all.return_value = []

    result = training_service.get_training_service(db, tid)

    lesson = result.sections[0]["lessons"][0]
    assert lesson["assessment_id"] == ASSESSMENT["id"]
    assert lesson["assessment"]["title"] == "Gentle Mobility Routines"
    assert "correct_answer" not in lesson["assessment"]["questions"][0]

    # Rule: top-level assessments[] must be unchanged (still has correct_answer)
    assert result.assessments[0]["questions"][0]["correct_answer"] == "some grading rubric text"
