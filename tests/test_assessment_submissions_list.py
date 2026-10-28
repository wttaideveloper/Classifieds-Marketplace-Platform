"""Staff list of every learner's attempts at one quiz / exam."""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.api.v1.endpoints import training as routes
from app.core.dependencies import get_current_user
from app.models.training_model import TrainingAssessmentSubmission, TrainingEnrolment
from tests.test_assessment_attempt_review import configure
from tests.test_training_progress_consistency import setup, TID  # noqa: F401

QUESTIONS = [{"id": "q1", "question_text": "Number?", "question_type": "single_choice", "options": [{"id": "a", "label": "1"}], "correct_answer": "1", "points": 2},
             {"id": "q2", "question_text": "Explain", "question_type": "essay", "points": 3}]
BASE = f"/api/v1/trainings/{TID}/assessments/assess-1/submissions"
STAFF = {"id": str(uuid4()), "role": "admin", "email": "admin@example.com", "tenant_id": str(uuid4())}


@pytest.fixture
def staff(setup):
    sessions, client = setup
    client.app.dependency_overrides[routes.require_training_manager] = lambda: STAFF
    configure(sessions, QUESTIONS)
    return sessions, client


def add_attempt(sessions, email, name, when, *, status="evaluated", score=3, total=5, passed=True, number=1, legacy=False):
    with sessions() as db:
        if not db.query(TrainingEnrolment).filter_by(training_id=TID, participant_email=email).first():
            db.add(TrainingEnrolment(training_id=TID, participant_name=name, participant_email=email, status="enrolled"))
        snapshot = None if legacy else {"status": status, "total_points": total, "passing_points": 3, "attempt_number": number, "assessment": {"id": "assess-1"}}
        row = TrainingAssessmentSubmission(training_id=TID, assessment_id="assess-1", participant_email=email, answers=[], score=str(score), passed=passed,
                                           submitted_at=when, review_snapshot=snapshot)
        db.add(row)
        db.commit()
        return str(row.id)


def seed(sessions):
    now = datetime(2026, 10, 20, 10, 0)
    return {
        "asha1": add_attempt(sessions, "asha@example.com", "Asha Rao", now, number=1, passed=False, score=2),
        "asha2": add_attempt(sessions, "asha@example.com", "Asha Rao", now + timedelta(days=1), number=2, score=4, passed=True),
        "ben": add_attempt(sessions, "ben@example.com", "Ben Carter", now + timedelta(days=2), status="pending", score=2, passed=False),
        "old": add_attempt(sessions, "cara@example.com", "Cara Lin", now - timedelta(days=5), legacy=True, passed=False),
    }


def test_the_list_has_every_field_newest_first_with_pagination(staff):
    sessions, client = staff
    ids = seed(sessions)

    body = client.get(BASE).json()

    assert [i["submission_id"] for i in body["items"]] == [ids["ben"], ids["asha2"], ids["asha1"], ids["old"]]
    assert body["pagination"] == {"total": 4, "page": 1, "page_size": 20, "total_pages": 1}
    assert body["status_counts"] == {"all": 4, "pending": 2, "evaluated": 2} and body["assessment_id"] == "assess-1"
    ben = body["items"][0]
    assert ben == {"submission_id": ids["ben"], "attempt_id": ids["ben"], "learner_name": "Ben Carter", "learner_email": "ben@example.com", "attempt_number": 1,
                   "status": "pending", "needs_manual_grading": True, "score": 2, "total_points": 5, "score_percent": 40, "passed": None,
                   "submitted_at": "2026-10-22T10:00:00", "is_legacy": False}
    asha = body["items"][1]
    assert asha["status"] == "evaluated" and asha["needs_manual_grading"] is False and asha["passed"] is True and asha["score_percent"] == 80 and asha["attempt_number"] == 2
    assert body["items"][2]["passed"] is False and body["items"][2]["attempt_number"] == 1


def test_an_old_attempt_has_no_result_but_still_has_a_number(staff):
    sessions, client = staff
    seed(sessions)

    old = next(i for i in client.get(BASE).json()["items"] if i["learner_email"] == "cara@example.com")

    assert old["is_legacy"] is True and old["status"] == "pending" and old["score"] is None and old["total_points"] is None and old["passed"] is None
    assert old["attempt_number"] == 1 and old["learner_name"] == "Cara Lin"


def test_status_filter(staff):
    sessions, client = staff
    seed(sessions)

    pending = client.get(BASE, params={"status": "pending"}).json()
    evaluated = client.get(BASE, params={"status": "evaluated"}).json()

    assert {i["learner_email"] for i in pending["items"]} == {"ben@example.com", "cara@example.com"} and pending["pagination"]["total"] == 2
    assert [i["status"] for i in evaluated["items"]] == ["evaluated", "evaluated"] and evaluated["status_counts"]["all"] == 4
    assert client.get(BASE, params={"status": "bogus"}).status_code == 422


def test_search_by_name_or_email_in_any_case(staff):
    sessions, client = staff
    seed(sessions)

    assert {i["learner_email"] for i in client.get(BASE, params={"search": "ASHA"}).json()["items"]} == {"asha@example.com"}
    assert {i["learner_email"] for i in client.get(BASE, params={"search": "carter"}).json()["items"]} == {"ben@example.com"}
    assert {i["learner_email"] for i in client.get(BASE, params={"search": "cara@exa"}).json()["items"]} == {"cara@example.com"}
    assert client.get(BASE, params={"search": "%"}).json()["items"] == []                       # a wildcard is just a character
    counts = client.get(BASE, params={"search": "asha", "status": "pending"}).json()
    assert counts["items"] == [] and counts["status_counts"] == {"all": 2, "pending": 0, "evaluated": 2}


def test_date_filters_accept_utc_forms_and_are_inclusive(staff):
    sessions, client = staff
    ids = seed(sessions)

    def found(**params):
        return {i["submission_id"] for i in client.get(BASE, params=params).json()["items"]}

    assert found(from_date="2026-10-21T10:00:00Z") == {ids["asha2"], ids["ben"]}                  # the boundary itself counts
    assert found(from_date="2026-10-21T15:30:00+05:30") == {ids["asha2"], ids["ben"]}             # 10:00 UTC written with an offset
    assert found(to_date="2026-10-20T10:00:00") == {ids["asha1"], ids["old"]}
    assert found(from_date="2026-10-21", to_date="2026-10-21") == {ids["asha2"]}                    # a bare date covers its whole day
    assert found(from_date="2026-10-20T00:00:00Z", to_date="2026-10-22T23:59:59Z") == {ids["asha1"], ids["asha2"], ids["ben"]}
    assert client.get(BASE, params={"from_date": "yesterday"}).status_code == 422
    assert client.get(BASE, params={"from_date": "2026-10-22", "to_date": "2026-10-20"}).status_code == 422


def test_paging_and_order(staff):
    sessions, client = staff
    ids = seed(sessions)

    first = client.get(BASE, params={"page": 1, "page_size": 3}).json()
    second = client.get(BASE, params={"page": 2, "page_size": 3}).json()
    oldest = client.get(BASE, params={"order": "oldest", "page_size": 1}).json()

    assert len(first["items"]) == 3 and [i["submission_id"] for i in second["items"]] == [ids["old"]]
    assert first["pagination"] == {"total": 4, "page": 1, "page_size": 3, "total_pages": 2}
    assert oldest["items"][0]["submission_id"] == ids["old"]
    assert client.get(BASE, params={"page_size": 101}).status_code == 422 and client.get(BASE, params={"page": 0}).status_code == 422


def test_an_empty_list_and_an_unknown_assessment(staff):
    sessions, client = staff

    empty = client.get(BASE).json()

    assert empty["items"] == [] and empty["pagination"] == {"total": 0, "page": 1, "page_size": 20, "total_pages": 0}
    assert empty["status_counts"] == {"all": 0, "pending": 0, "evaluated": 0}
    assert client.get(f"/api/v1/trainings/{TID}/assessments/nope/submissions").status_code == 404


def test_attempts_of_other_assessments_are_not_listed(staff):
    sessions, client = staff
    seed(sessions)
    with sessions() as db:
        db.add(TrainingAssessmentSubmission(training_id=TID, assessment_id="other", participant_email="x@example.com", answers=[], score="1", passed=True,
                                            submitted_at=datetime(2026, 10, 20), review_snapshot={"status": "evaluated"}))
        db.commit()

    assert client.get(BASE).json()["pagination"]["total"] == 4


def test_the_listed_id_opens_the_staff_review_and_a_learner_cannot_list(staff, monkeypatch):
    sessions, client = staff
    ids = seed(sessions)
    monkeypatch.setattr("app.repository.training_repo.require_training_owner", lambda *a, **k: None)
    learner = client.app.dependency_overrides[get_current_user]
    client.app.dependency_overrides[get_current_user] = lambda: STAFF

    review = client.get(f"{BASE}/{ids['asha2']}/review")
    assert review.status_code == 200 and review.json()["submission_id"] == ids["asha2"]

    client.app.dependency_overrides.pop(routes.require_training_manager)
    client.app.dependency_overrides[get_current_user] = lambda: {"id": str(uuid4()), "role": "customer", "email": "asha@example.com"}
    assert client.get(BASE).status_code == 403
