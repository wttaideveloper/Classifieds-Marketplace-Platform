"""Training / course reviews follow the common review policy.

A new review is pending and public only once an Enterprise Admin (of the owning business) or a Super Admin approves
it; the average counts approved reviews only and is null when there are none; no email addresses in public output;
comment <= 2000 characters; one review per learner; editing keeps the status.
"""
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import training as training_routes
from app.core.catalog_access import get_optional_catalog_user
from app.core.dependencies import get_current_user
from app.db.database import Base, get_db
from app.models.enterprise_model import Enterprise
from app.models.training_model import (
    Training, TrainingAssessmentSubmission, TrainingAssignmentSubmission, TrainingEnrolment, TrainingLessonAttendance,
    TrainingLiveSession, TrainingOrder, TrainingProgress, TrainingReview, TrainingWaitlist,
)
from app.services import review_notifications

TENANT, OTHER_TENANT = uuid4(), uuid4()
LEARNER = {"id": str(uuid4()), "role": "customer", "email": "learner@example.com", "name": "Asha Rao"}


def as_user(client, user):
    """Switch who the next requests are made as (both dependencies the review routes use)."""
    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_optional_catalog_user] = lambda: user


def staff(role="admin", tenant=TENANT):
    return {"id": str(uuid4()), "role": role, "email": f"{role}@example.com", "tenant_id": str(tenant)}


@pytest.fixture
def dispatched(monkeypatch):
    """Review notifications run on a background thread against the real database; record the calls instead."""
    calls = []
    monkeypatch.setattr(review_notifications, "_dispatch", lambda fn, *a, **k: calls.append((fn.__name__, a)))
    return calls


@pytest.fixture
def world(monkeypatch, dispatched):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[m.__table__ for m in (
        Enterprise, Training, TrainingEnrolment, TrainingProgress, TrainingAssessmentSubmission,
        TrainingAssignmentSubmission, TrainingLessonAttendance, TrainingReview, TrainingOrder,
        TrainingLiveSession, TrainingWaitlist,
    )])
    sessions = sessionmaker(bind=engine)
    tid = uuid4()
    with sessions() as db:
        db.add(Training(
            id=tid, enterprise_id=uuid4(), tenant_id=TENANT, title="Ergonomics 101", category="Wellness",
            status="published", delivery_mode="online", sections=[], assessments=[], assignments=[],
        ))
        db.add(TrainingEnrolment(training_id=tid, participant_name="Asha Rao", participant_email=LEARNER["email"], status="enrolled"))
        db.commit()

    app = FastAPI()
    app.include_router(training_routes.router, prefix="/api/v1/trainings")
    app.include_router(training_routes.router, prefix="/api/v1/courses")

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        as_user(client, LEARNER)
        yield client, sessions, tid
    engine.dispose()


def post(client, tid, rating=5, comment="Clear and practical.", prefix="trainings"):
    return client.post(f"/api/v1/{prefix}/{tid}/reviews", json={"rating": rating, "comment": comment})


def listing(client, tid, prefix="trainings"):
    return client.get(f"/api/v1/{prefix}/{tid}/reviews").json()


def moderate(client, tid, review_id, action, user, prefix="trainings"):
    as_user(client, user)
    resp = client.patch(f"/api/v1/{prefix}/{tid}/reviews/{review_id}/moderate", json={"action": action})
    as_user(client, LEARNER)
    return resp


# --- a new review is pending ---------------------------------------------------------------------

def test_a_new_review_is_pending_and_not_public(world):
    client, _, tid = world

    resp = post(client, tid)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["moderation_status"] == "pending"
    assert body["participant_name"] == "Asha Rao" and body["verified"] is True
    assert body["participant_email"] == LEARNER["email"]  # the author sees their own
    assert listing(client, tid) == {
        "reviews": [], "average_rating": None, "count": 0, "rating_distribution": {"5": 0, "4": 0, "3": 0, "2": 0, "1": 0},
        "pagination": {"total": 0, "page": 1, "page_size": 0, "total_pages": 0},
    }


def test_after_approval_it_is_public_without_an_email_and_counts(world):
    client, _, tid = world
    review_id = post(client, tid, rating=4).json()["id"]

    resp = moderate(client, tid, review_id, "approved", staff())

    assert resp.status_code == 200, resp.text
    assert resp.json()["moderation_status"] == "approved"
    body = listing(client, tid)
    assert body["count"] == 1 and body["average_rating"] == 4.0
    assert body["rating_distribution"] == {"5": 0, "4": 1, "3": 0, "2": 0, "1": 0}
    [item] = body["reviews"]
    assert item["participant_email"] is None and item["participant_name"] == "Asha Rao"
    assert item["moderation_status"] == "approved"
    assert LEARNER["email"] not in client.get(f"/api/v1/trainings/{tid}/reviews").text


def test_reject_and_reset_hide_it_again(world):
    client, _, tid = world
    review_id = post(client, tid).json()["id"]
    moderate(client, tid, review_id, "approved", staff())

    moderate(client, tid, review_id, "rejected", staff())
    assert listing(client, tid)["count"] == 0
    moderate(client, tid, review_id, "approved", staff())
    assert listing(client, tid)["count"] == 1
    moderate(client, tid, review_id, "pending", staff())
    assert listing(client, tid)["average_rating"] is None


def test_the_average_counts_approved_reviews_only(world):
    client, sessions, tid = world
    first = post(client, tid, rating=5).json()["id"]
    moderate(client, tid, first, "approved", staff())
    with sessions() as db:
        db.add(TrainingEnrolment(training_id=tid, participant_name="Ravi", participant_email="ravi@example.com", status="enrolled"))
        db.commit()
    as_user(client, {"id": str(uuid4()), "role": "customer", "email": "ravi@example.com"})
    post(client, tid, rating=1)  # stays pending
    as_user(client, LEARNER)

    body = listing(client, tid)

    assert body["count"] == 1 and body["average_rating"] == 5.0


def test_the_training_detail_and_cards_use_approved_reviews_only(world):
    client, _, tid = world
    review_id = post(client, tid, rating=3).json()["id"]
    detail = client.get(f"/api/v1/trainings/{tid}").json()
    assert detail["average_rating"] is None and detail["reviews_count"] == 0 and detail["reviews"] == []

    moderate(client, tid, review_id, "approved", staff())
    detail = client.get(f"/api/v1/trainings/{tid}").json()
    assert detail["average_rating"] == 3.0 and detail["reviews_count"] == 1
    assert "participant_email" not in detail["reviews"][0] and detail["reviews"][0]["participant_name"] == "Asha Rao"


# --- edit, limits, eligibility -------------------------------------------------------------------

def test_editing_updates_the_same_review_and_keeps_its_status(world):
    client, sessions, tid = world
    review_id = post(client, tid, rating=5, comment="first").json()["id"]
    moderate(client, tid, review_id, "approved", staff())

    again = post(client, tid, rating=2, comment="changed my mind")

    assert again.status_code == 201 and again.json()["id"] == review_id
    assert again.json()["moderation_status"] == "approved"
    with sessions() as db:
        assert db.query(TrainingReview).count() == 1
    assert listing(client, tid)["reviews"][0]["comment"] == "changed my mind"


@pytest.mark.parametrize("rating", [0, 6, -1])
def test_a_rating_outside_1_to_5_is_422(world, rating):
    client, _, tid = world
    assert post(client, tid, rating=rating).status_code == 422


def test_the_comment_limit_is_2000_characters(world):
    client, _, tid = world
    assert post(client, tid, comment="x" * 2001).status_code == 422
    assert post(client, tid, comment="x" * 2000).status_code == 201


def test_a_blank_comment_is_stored_as_none(world):
    client, _, tid = world
    assert post(client, tid, comment="   ").json()["comment"] is None


@pytest.mark.parametrize("status", ["pending_approval", "rejected", "cancelled", "waitlisted"])
def test_only_active_learners_can_review(world, status):
    client, sessions, tid = world
    with sessions() as db:
        db.query(TrainingEnrolment).update({"status": status})
        db.commit()

    resp = post(client, tid)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Verified reviews only — must be enrolled to review"


# --- who may moderate ----------------------------------------------------------------------------

def test_providers_the_wrong_business_and_customers_cannot_moderate(world):
    client, _, tid = world
    review_id = post(client, tid).json()["id"]

    for user in (staff("provider"), staff("admin", OTHER_TENANT), LEARNER):
        assert moderate(client, tid, review_id, "approved", user).status_code == 403, user
    assert listing(client, tid)["count"] == 0


def test_a_super_admin_can_moderate_any_business(world):
    client, _, tid = world
    review_id = post(client, tid).json()["id"]
    root = {"id": str(uuid4()), "role": "super_admin", "email": "root@example.com"}

    assert moderate(client, tid, review_id, "approved", root).status_code == 200
    assert listing(client, tid)["count"] == 1


def test_moderate_checks_the_review_belongs_to_the_training(world):
    client, sessions, tid = world
    other = uuid4()
    with sessions() as db:
        db.add(Training(id=other, enterprise_id=uuid4(), tenant_id=TENANT, title="Other", category="x", status="published",
                        delivery_mode="online", sections=[], assessments=[], assignments=[]))
        db.commit()
    review_id = post(client, tid).json()["id"]

    assert moderate(client, other, review_id, "approved", staff()).status_code == 404
    assert moderate(client, tid, str(uuid4()), "approved", staff()).status_code == 404
    assert moderate(client, tid, review_id, "published", staff()).status_code == 400


def test_the_course_paths_are_the_same_reviews(world):
    client, _, tid = world
    review_id = post(client, tid, prefix="courses").json()["id"]

    assert moderate(client, tid, review_id, "approved", staff(), prefix="courses").status_code == 200
    assert listing(client, tid, prefix="trainings")["count"] == 1
    assert listing(client, tid, prefix="courses")["count"] == 1


# --- my review -----------------------------------------------------------------------------------

def test_my_review_is_null_until_written_then_shows_the_status(world):
    client, _, tid = world
    assert client.get(f"/api/v1/trainings/{tid}/reviews/me").json() is None

    post(client, tid, rating=4)
    mine = client.get(f"/api/v1/trainings/{tid}/reviews/me").json()

    assert mine["rating"] == 4 and mine["moderation_status"] == "pending"
    assert mine["participant_email"] == LEARNER["email"]


# --- notifications -------------------------------------------------------------------------------

def test_a_new_review_notifies_the_business_but_an_edit_does_not(world, dispatched):
    client, _, tid = world
    review_id = post(client, tid).json()["id"]
    post(client, tid, rating=2)

    assert [name for name, _ in dispatched] == ["_deliver_submitted"]
    assert dispatched[0][1][:2] == ("training", review_id)


def test_only_a_real_approve_or_reject_notifies_the_author(world, dispatched):
    client, _, tid = world
    review_id = post(client, tid).json()["id"]
    dispatched.clear()

    moderate(client, tid, review_id, "approved", staff())
    moderate(client, tid, review_id, "approved", staff())   # same status again: nothing new to say
    moderate(client, tid, review_id, "pending", staff())    # reset: not announced
    moderate(client, tid, review_id, "rejected", staff())

    assert [(name, args[2]) for name, args in dispatched] == [("_deliver_decision", "approved"), ("_deliver_decision", "rejected")]


def test_simultaneous_first_posts_update_the_winner_instead_of_failing(world, monkeypatch):
    from sqlalchemy.orm import Query

    client, sessions, tid = world
    first = post(client, tid, rating=5, comment="the other request won")
    original, hidden = Query.first, {"done": False}

    def first_lookup_misses(self):
        if self.column_descriptions[0].get("entity") is TrainingReview and not hidden["done"]:
            hidden["done"] = True
            return None
        return original(self)

    monkeypatch.setattr(Query, "first", first_lookup_misses)
    second = post(client, tid, rating=2, comment="second")

    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"] and second.json()["rating"] == 2
    with sessions() as db:
        assert db.query(TrainingReview).count() == 1


# --- sorting, filtering and paging the public list -----------------------------------------------

def seed_ordered(sessions, tid):
    """Five approved reviews, oldest first: 5*, 3*, 4*, 3*, 1* (the 5* and the 3*s have comments)."""
    from datetime import datetime

    with sessions() as db:
        for minute, (rating, comment) in enumerate([(5, "five"), (3, "three-a"), (4, None), (3, "three-b"), (1, None)]):
            db.add(TrainingReview(training_id=tid, participant_email=f"p{minute}@example.com", rating=str(rating), comment=comment,
                                  moderation_status="approved", created_at=datetime(2026, 1, 1, 0, minute)))
        db.commit()


def ratings(client, tid, **params):
    body = client.get(f"/api/v1/trainings/{tid}/reviews", params=params).json()
    return [r["rating"] for r in body["reviews"]], body


def test_sorting_filtering_and_paging_do_not_change_the_summary(world):
    client, sessions, tid = world
    seed_ordered(sessions, tid)

    assert ratings(client, tid)[0] == [1, 3, 4, 3, 5]
    assert ratings(client, tid, sort="oldest")[0] == [5, 3, 4, 3, 1]
    assert ratings(client, tid, sort="highest")[0] == [5, 4, 3, 3, 1]
    assert ratings(client, tid, sort="lowest")[0] == [1, 3, 3, 4, 5]
    assert client.get(f"/api/v1/trainings/{tid}/reviews", params={"sort": "best"}).status_code == 422

    got, body = ratings(client, tid, rating=3)
    assert got == [3, 3] and body["pagination"]["total"] == 2
    assert body["count"] == 5 and body["average_rating"] == 3.2
    assert body["rating_distribution"] == {"5": 1, "4": 1, "3": 2, "2": 0, "1": 1}
    assert ratings(client, tid, with_comment="true")[0] == [3, 3, 5]

    assert ratings(client, tid)[1]["pagination"] == {"total": 5, "page": 1, "page_size": 5, "total_pages": 1}
    got, body = ratings(client, tid, page=2, page_size=2)
    assert got == [4, 3] and body["pagination"] == {"total": 5, "page": 2, "page_size": 2, "total_pages": 3}
    assert ratings(client, tid, page=9, page_size=2)[0] == []


def test_the_course_path_accepts_the_same_options(world):
    client, sessions, tid = world
    seed_ordered(sessions, tid)

    body = client.get(f"/api/v1/courses/{tid}/reviews", params={"sort": "highest", "page": 1, "page_size": 2}).json()

    assert [r["rating"] for r in body["reviews"]] == [5, 4] and body["count"] == 5
