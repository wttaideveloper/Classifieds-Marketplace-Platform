"""Production bug: GET /my/enrolments and GET /{id}/content disagreed on
progress_percent for the same completed_lessons/total_lessons (8/12 => 66.67
vs 8/12 => 100.0), and 4 of a learner's 12 completed lessons (2 quizzes, 2
assignments) never reached TrainingProgress.lessons_completed because
submit_assessment_service/submit_assignment_service only ever wrote to their
own submission tables.

Root causes fixed:
1. training_curriculum.learning_response() recomputed progress_percent from a
   mandatory-only ("required") subset and overwrote the correct raw
   completed/total value already set by get_secure_training_content_service —
   removed; completed_required_items/total_required_items still expose the
   mandatory-completion business rule under their own names.
2. submit_assessment_service / submit_assignment_service never called into
   TrainingProgress — a learner who "completed" a quiz or assignment lesson by
   submitting it was never credited in lessons_completed, only in the separate
   submission tables. Both now call the same _apply_lesson_completion helper
   complete_lesson_service uses, so lessons_completed is the single source of
   truth for /my/enrolments, /content, and /progress alike.
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
    Training, TrainingEnrolment, TrainingProgress,
    TrainingAssessmentSubmission, TrainingAssignmentSubmission,
)
from app.services import training_service as service

TID = UUID('d75ea47d-0831-4715-b43f-68455f70819e')
EMAIL = 'learner@example.com'

# 8 plain lessons + 2 quiz lessons + 2 assignment lessons = 12 total, mirroring
# the reported training's 8-completed / 12-total shape.
PLAIN_LESSONS = [{"id": f"lesson-{i}", "type": "topic", "title": f"Lesson {i}"} for i in range(1, 9)]
QUIZ_LESSONS = [
    {"id": "quiz-1", "type": "quiz", "title": "Quiz 1", "assessment_id": "assess-1"},
    {"id": "quiz-2", "type": "quiz", "title": "Quiz 2", "assessment_id": "assess-2"},
]
ASSIGNMENT_LESSONS = [
    {"id": "assign-1", "type": "assignment", "title": "Assignment 1", "assignment_id": "asg-1"},
    {"id": "assign-2", "type": "assignment", "title": "Assignment 2", "assignment_id": "asg-2"},
]
ALL_LESSON_IDS = [l["id"] for l in PLAIN_LESSONS + QUIZ_LESSONS + ASSIGNMENT_LESSONS]

ASSESSMENTS = [
    {"id": "assess-1", "type": "quiz", "pass_percent": 50,
     "questions": [{"id": "q1", "question_type": "mcq", "correct_answer": "Mars", "points": 1}]},
    {"id": "assess-2", "type": "quiz", "pass_percent": 50,
     "questions": [{"id": "q1", "question_type": "mcq", "correct_answer": "Mars", "points": 1}]},
]
ASSIGNMENTS = [
    {"id": "asg-1", "title": "Assignment 1", "accepted_file_types": [], "due_date": None, "allow_late_submissions": True},
    {"id": "asg-2", "title": "Assignment 2", "accepted_file_types": [], "due_date": None, "allow_late_submissions": True},
]


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, 'visit_JSONB', lambda *a, **kw: 'JSON', raising=False)
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[m.__table__ for m in (
        Enterprise, Training, TrainingEnrolment, TrainingProgress,
        TrainingAssessmentSubmission, TrainingAssignmentSubmission,
    )])
    sessions = sessionmaker(bind=engine)
    user = {'id': str(uuid4()), 'role': 'customer', 'email': EMAIL}
    with sessions() as db:
        db.add(Training(
            id=TID, enterprise_id=uuid4(), tenant_id=uuid4(), title='Ergonomics & Desk Health',
            category='Wellness', status='published', delivery_mode='self_paced',
            sections=[
                {"id": "sec-1", "type": "section", "lessons": PLAIN_LESSONS},
                {"id": "sec-2", "type": "section", "lessons": QUIZ_LESSONS + ASSIGNMENT_LESSONS},
            ],
            assessments=ASSESSMENTS, assignments=ASSIGNMENTS,
        ))
        db.add(TrainingEnrolment(training_id=TID, participant_name='Learner', participant_email=EMAIL, status='enrolled'))
        db.commit()
    app = FastAPI()
    app.include_router(routes.router, prefix='/api/v1/trainings')
    def database():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        yield sessions, client
    engine.dispose()


def _complete_plain_lessons(client, n):
    for lesson_id in ALL_LESSON_IDS[:n]:
        resp = client.post('/api/v1/trainings/{}/progress/complete-lesson'.format(TID), json={'lesson_id': lesson_id})
        assert resp.status_code == 200, resp.text


def _submit_quiz(client, aid):
    return client.post(f'/api/v1/trainings/{TID}/assessments/{aid}/submit', json={'answers': [{'question_id': 'q1', 'answer': 'Mars'}]})


def _submit_assignment(client, aid):
    return client.post(f'/api/v1/trainings/{TID}/assignments/{aid}/submit', json={'submission_text': 'done'})


def _my_enrolment_card(client):
    resp = client.get('/api/v1/trainings/my/enrolments')
    assert resp.status_code == 200, resp.text
    return next(row for row in resp.json() if row['training_id'] == str(TID))


def _content(client):
    resp = client.get(f'/api/v1/trainings/{TID}/content')
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- 1 & 5: 8/12 completed => both APIs agree on 66.67, mandatory rule untouched ---

def test_eight_of_twelve_reports_consistent_percent_on_both_apis(setup):
    sessions, client = setup
    _complete_plain_lessons(client, 8)  # only the 8 plain lessons — quizzes/assignments untouched

    card = _my_enrolment_card(client)
    assert card['completed_lessons'] == 8
    assert card['total_lessons'] == 12
    assert card['progress_percent'] == 66.67

    content = _content(client)
    assert content['completed_lessons'] == 8
    assert content['total_lessons'] == 12
    assert content['progress_percent'] == 66.67  # was 100.0 before the fix

    # Mandatory-completion business rule (separate from progress_percent) is unaffected —
    # nothing here is marked mandatory, so it reports its own 0/0 shape untouched.
    assert content['total_required_items'] == 0
    assert content['completed_required_items'] == 0


# --- 2: completing the remaining 4 (2 quizzes + 2 assignments) reaches 12/12/100 on both ---

def test_completing_remaining_quiz_and_assignment_lessons_reaches_full_completion(setup):
    sessions, client = setup
    _complete_plain_lessons(client, 8)
    assert _submit_quiz(client, 'assess-1').status_code == 201
    assert _submit_quiz(client, 'assess-2').status_code == 201
    assert _submit_assignment(client, 'asg-1').status_code == 201
    assert _submit_assignment(client, 'asg-2').status_code == 201

    card = _my_enrolment_card(client)
    assert card['completed_lessons'] == 12
    assert card['total_lessons'] == 12
    assert card['progress_percent'] == 100.0

    content = _content(client)
    assert content['completed_lessons'] == 12
    assert content['total_lessons'] == 12
    assert content['progress_percent'] == 100.0

    with sessions() as db:
        prog = db.query(TrainingProgress).filter(TrainingProgress.training_id == TID, TrainingProgress.participant_email == EMAIL).one()
        assert set(prog.lessons_completed) == set(ALL_LESSON_IDS)


# --- 3: every supported lesson type reaching completion persists into lessons_completed ---

def test_quiz_and_assignment_submissions_persist_into_lessons_completed(setup):
    sessions, client = setup
    _submit_quiz(client, 'assess-1')
    _submit_assignment(client, 'asg-1')

    with sessions() as db:
        prog = db.query(TrainingProgress).filter(TrainingProgress.training_id == TID, TrainingProgress.participant_email == EMAIL).one()
        assert 'quiz-1' in prog.lessons_completed
        assert 'assign-1' in prog.lessons_completed
        assert 'quiz-2' not in prog.lessons_completed
        assert 'assign-2' not in prog.lessons_completed


# --- 4: duplicate completion remains idempotent ---

def test_duplicate_quiz_submission_does_not_duplicate_completed_lesson_id(setup):
    sessions, client = setup
    assert _submit_quiz(client, 'assess-1').status_code == 201
    assert _submit_quiz(client, 'assess-1').status_code == 201  # resubmission allowed, still one completion record

    with sessions() as db:
        prog = db.query(TrainingProgress).filter(TrainingProgress.training_id == TID, TrainingProgress.participant_email == EMAIL).one()
        assert prog.lessons_completed.count('quiz-1') == 1

    card = _my_enrolment_card(client)
    assert card['completed_lessons'] == 1


def test_duplicate_complete_lesson_call_is_idempotent(setup):
    sessions, client = setup
    first = client.post(f'/api/v1/trainings/{TID}/progress/complete-lesson', json={'lesson_id': 'lesson-1'})
    second = client.post(f'/api/v1/trainings/{TID}/progress/complete-lesson', json={'lesson_id': 'lesson-1'})
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()['lessons_done'] == 1


# --- 7: training with zero/empty sections does not produce invalid progress ---

def test_empty_sections_training_reports_zero_progress_not_a_crash(setup):
    sessions, client = setup
    other_tid = uuid4()
    with sessions() as db:
        db.add(Training(id=other_tid, enterprise_id=uuid4(), tenant_id=uuid4(), title='Empty', category='X', status='published', delivery_mode='self_paced', sections=[], assessments=[], assignments=[]))
        db.add(TrainingEnrolment(training_id=other_tid, participant_name='Learner', participant_email=EMAIL, status='enrolled'))
        db.commit()

    resp = client.get('/api/v1/trainings/my/enrolments')
    assert resp.status_code == 200
    row = next(r for r in resp.json() if r['training_id'] == str(other_tid))
    assert row['total_lessons'] == 0
    assert row['completed_lessons'] == 0
    assert row['progress_percent'] == 0

    content = client.get(f'/api/v1/trainings/{other_tid}/content')
    assert content.status_code == 200
    assert content.json()['total_lessons'] == 0
    assert content.json()['progress_percent'] == 0


# --- 8: completing one lesson cannot overwrite previously completed lesson IDs ---

def test_completing_a_new_lesson_preserves_previously_completed_ids(setup):
    sessions, client = setup
    client.post(f'/api/v1/trainings/{TID}/progress/complete-lesson', json={'lesson_id': 'lesson-1'})
    client.post(f'/api/v1/trainings/{TID}/progress/complete-lesson', json={'lesson_id': 'lesson-2'})
    _submit_quiz(client, 'assess-1')

    with sessions() as db:
        prog = db.query(TrainingProgress).filter(TrainingProgress.training_id == TID, TrainingProgress.participant_email == EMAIL).one()
        assert set(prog.lessons_completed) == {'lesson-1', 'lesson-2', 'quiz-1'}


# --- mandatory-completion business rule (certificate eligibility) is unchanged ---

def test_mandatory_only_completion_still_triggers_certificate_without_full_completion(setup):
    """complete_lesson_service's own completed_at/certificate_url rule — completing
    every MANDATORY lesson certifies even if optional lessons remain — must survive
    the progress_percent fix untouched. progress_percent itself is NOT expected to
    read 100 here; that is exactly the distinction the production bug conflated."""
    sessions, client = setup
    with sessions() as db:
        training = db.get(Training, TID)
        training.sections = [{
            "id": "sec-1", "type": "section",
            "lessons": [
                {"id": "mand-1", "type": "topic", "title": "Mandatory 1", "is_mandatory": True},
                {"id": "mand-2", "type": "topic", "title": "Mandatory 2", "is_mandatory": True},
                {"id": "optional-1", "type": "topic", "title": "Optional 1", "is_mandatory": False},
            ],
        }]
        db.commit()

    r1 = client.post(f'/api/v1/trainings/{TID}/progress/complete-lesson', json={'lesson_id': 'mand-1'})
    r2 = client.post(f'/api/v1/trainings/{TID}/progress/complete-lesson', json={'lesson_id': 'mand-2'})
    assert r2.json()['mandatory_done'] == 2
    assert r2.json()['mandatory_total'] == 2
    assert r2.json()['completed_at'] is not None
    assert r2.json()['certificate_url'] is not None

    content = _content(client)
    assert content['completed_required_items'] == 2
    assert content['total_required_items'] == 2
    # Raw progress_percent correctly reflects 2 of 3 lessons — the mandatory rule
    # is exposed separately, not folded into this field.
    assert content['progress_percent'] == 66.67
