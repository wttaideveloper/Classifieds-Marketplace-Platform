from uuid import uuid4

from sqlalchemy.orm.attributes import flag_modified

from app.models.training_model import Training, TrainingAssessmentSubmission, TrainingEnrolment
from app.core.dependencies import get_current_user
from tests.test_training_progress_consistency import setup, TID, EMAIL  # noqa: F401


def configure(sessions, questions, **extra):
    with sessions() as db:
        training = db.get(Training, TID)
        training.assessments = [{"id": "assess-1", "title": "Quiz", "attempt_limit": 3,
                                 "pass_percent": 50, "questions": questions, **extra}]
        flag_modified(training, "assessments")
        db.commit()


def test_submit_list_and_review_use_saved_snapshot(setup):
    sessions, client = setup
    questions = [
        {"id": "q1", "question_text": "Number?", "question_type": "single_choice", "options": [{"id": "a", "label": "1"}, {"id": "b", "label": "2"}], "correct_answer": "1", "points": 1},
        {"id": "q2", "question_text": "Choose", "question_type": "multiple_select", "options": [{"id": "a", "label": "1"}, {"id": "b", "label": "2"}], "correct_answer": "1,2", "points": 2},
        {"id": "q3", "question_text": "True?", "question_type": "true_false", "options": [], "correct_answer": "true", "points": 1},
    ]
    configure(sessions, questions)
    base = f"/api/v1/trainings/{TID}"
    empty = next(item for item in client.get(base + "/assignments").json() if item["id"] == "assess-1")
    assert empty["attempts_allowed"] == 3 and empty["attempts_made"] == 0 and not empty["can_review"]
    assert client.get(base + "/assignments/assess-1/attempts").json() == []
    answers = [{"question_id": "q1", "answer": "a"}, {"question_id": "q2", "answer": "a,b"}, {"question_id": "q3", "answer": "true"}]
    submitted = client.post(base + "/assessments/assess-1/submit", json={"answers": answers})
    assert submitted.status_code == 201, submitted.text
    attempt_id = submitted.json()["submission_id"]
    items = client.get(base + "/assignments/assess-1/attempts").json()
    assert len(items) == 1 and items[0]["score"] == 4 and items[0]["score_percent"] == 100
    summary = next(item for item in client.get(base + "/assignments").json() if item["id"] == "assess-1")
    assert summary["latest_attempt_id"] == attempt_id and summary["can_review"] and summary["attempts_made"] == 1
    configure(sessions, [{"id": "changed", "question_text": "Changed", "points": 99}])
    review = client.get(base + f"/assignments/assess-1/attempts/{attempt_id}").json()
    assert [q["id"] for q in review["questions"]] == ["q1", "q2", "q3"]
    assert [q["user_answer"] for q in review["questions"]] == ["a", "a,b", "true"]
    assert [q["points_awarded"] for q in review["questions"]] == [1, 2, 1]
    with sessions() as db:
        assert db.query(TrainingAssessmentSubmission).count() == 1


def test_pending_and_cross_user_attempts(setup):
    sessions, client = setup
    configure(sessions, [{"id": "q1", "question_type": "essay", "question_text": "Explain", "points": 2}])
    base = f"/api/v1/trainings/{TID}"
    result = client.post(base + "/assessments/assess-1/submit", json={"answers": [{"question_id": "q1", "answer": "essay"}]})
    assert result.status_code == 201
    attempt_id = result.json()["submission_id"]
    row = client.get(base + f"/assignments/assess-1/attempts/{attempt_id}").json()
    assert row["status"] == "pending" and row["score"] is None and row["passed"] is None
    assert row["questions"][0]["user_answer"] == "essay" and row["questions"][0]["correct_answer"] is None
    assert client.get(base + f"/assignments/assess-2/attempts/{attempt_id}").status_code == 404
    assert client.get(base + f"/assignments/assess-1/attempts/{uuid4()}").status_code == 404


def test_attempt_limit_order_zero_score_and_user_isolation(setup):
    sessions, client = setup
    configure(sessions, [{"id": "q1", "question_type": "mcq", "question_text": "Pick", "options": ["yes", "no"], "correct_answer": "yes", "points": 1}])
    base = f"/api/v1/trainings/{TID}"
    url = base + "/assessments/assess-1/submit"
    identifiers = []
    for _ in range(3):
        result = client.post(url, json={"answers": [{"question_id": "q1", "answer": "no"}]})
        assert result.status_code == 201
        identifiers.append(result.json()["submission_id"])
    assert client.post(url, json={"answers": []}).status_code == 400
    listing = client.get(base + "/assignments/assess-1/attempts").json()
    assert [x["attempt_number"] for x in listing] == [3, 2, 1]
    assert listing[0]["score"] == 0 and listing[0]["passed"] is False
    with sessions() as db:
        db.add(TrainingEnrolment(training_id=TID, participant_name="Other", participant_email="other@example.com", status="enrolled"))
        db.commit()
    client.app.dependency_overrides[get_current_user] = lambda: {"id": str(uuid4()), "role": "customer", "email": "other@example.com"}
    assert client.get(base + "/assignments/assess-1/attempts").json() == []
    assert client.get(base + f"/assignments/assess-1/attempts/{identifiers[0]}").status_code == 404
    client.app.dependency_overrides[get_current_user] = lambda: {"id": str(uuid4()), "role": "customer", "email": "outside@example.com"}
    assert client.get(base + "/assignments/assess-1/attempts").status_code == 403


def test_historical_attempt_does_not_regrade_current_questions(setup):
    sessions, client = setup
    configure(sessions, [{"id": "q1", "question_text": "New version", "correct_answer": "secret", "points": 5}])
    with sessions() as db:
        row = TrainingAssessmentSubmission(training_id=TID, assessment_id="assess-1", participant_email=EMAIL,
                                           answers=[{"question_id": "q1", "answer": "old"}], score="1", passed=True)
        db.add(row)
        db.commit()
        attempt_id = str(row.id)
    response = client.get(f"/api/v1/trainings/{TID}/assignments/assess-1/attempts/{attempt_id}")
    assert response.status_code == 200
    assert response.json()["score"] is None and response.json()["questions"] == []


def test_pending_cannot_use_legacy_review_to_reveal_answers(setup):
    sessions, client = setup
    configure(sessions, [{"id": "q1", "question_type": "essay", "question_text": "Explain", "points": 2}])
    base = f"/api/v1/trainings/{TID}"
    row = client.post(base + "/assessments/assess-1/submit", json={"answers": [{"question_id": "q1", "answer": "text"}]}).json()
    assert client.get(base + f"/assessments/assess-1/submissions/{row['submission_id']}/review").status_code == 403


def test_manual_evaluation_and_manual_release_policy(setup):
    from app.services.training_service import grade_assessment_manual_service
    sessions, client = setup
    configure(sessions, [{"id": "q1", "question_type": "essay", "question_text": "Explain", "points": 2}])
    base = f"/api/v1/trainings/{TID}"
    row = client.post(base + "/assessments/assess-1/submit", json={"answers": [{"question_id": "q1", "answer": "text"}]}).json()
    with sessions() as db:
        grade_assessment_manual_service(db, TID, "assess-1", row["submission_id"], 2, "Good")
    reviewed = client.get(base + f"/assignments/assess-1/attempts/{row['submission_id']}").json()
    assert reviewed["status"] == "evaluated" and reviewed["score"] == 2 and reviewed["feedback"] == "Good"
    assert reviewed["questions"][0]["points_awarded"] is None
    configure(sessions, [{"id": "q1", "question_type": "mcq", "options": ["yes", "no"], "correct_answer": "yes", "points": 1}], publication="manual")
    pending_release = client.post(base + "/assessments/assess-1/submit", json={"answers": [{"question_id": "q1", "answer": "yes"}]}).json()
    hidden = client.get(base + f"/assignments/assess-1/attempts/{pending_release['submission_id']}").json()
    assert hidden["status"] == "evaluated" and hidden["score"] is None and hidden["questions"][0]["correct_answer"] is None
    assignment = next(item for item in client.get(base + "/assignments").json() if item["id"] == "assess-1")
    assert assignment["latest_score"] is None and assignment["can_review"] is True


def test_review_is_read_only_and_enrolment_identity_is_checked(setup):
    sessions, client = setup
    configure(sessions, [{"id": "q1", "question_type": "true_false", "correct_answer": "yes", "points": 1}])
    base = f"/api/v1/trainings/{TID}"
    response = client.post(base + "/assessments/assess-1/submit", json={"answers": [{"question_id": "q1", "answer": "1"}]})
    attempt_id = response.json()["submission_id"]
    with sessions() as db:
        before = db.query(TrainingAssessmentSubmission).one()
        saved = (before.answers, before.score, before.passed, before.review_snapshot)
    for _ in range(2):
        review = client.get(base + f"/assignments/assess-1/attempts/{attempt_id}")
        assert review.status_code == 200
        assert review.json()["questions"][0]["user_answer"] == "true"
    with sessions() as db:
        after = db.query(TrainingAssessmentSubmission).one()
        assert (after.answers, after.score, after.passed, after.review_snapshot) == saved
        db.query(TrainingEnrolment).filter_by(training_id=TID, participant_email=EMAIL).one().user_id = uuid4()
        db.commit()
    client.app.dependency_overrides[get_current_user] = lambda: {"id": str(uuid4()), "role": "customer", "email": EMAIL}
    assert client.get(base + "/assignments/assess-1/attempts").status_code == 403
