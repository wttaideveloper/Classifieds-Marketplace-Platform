"""Training / course reviews follow the common review policy.

A new review is approved and public at once; only a Super Admin changes a status afterwards (Enterprise Admins can
look but not moderate); the average counts approved reviews only and is null when there are none; no email addresses
in public output; comment <= 2000 characters; one review per learner; editing keeps the status (a rejected review
goes back to pending).
"""
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import training as training_routes
from app.core.catalog_access import get_optional_catalog_user
from app.core.dependencies import get_current_user, get_optional_current_user
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
    client.app.dependency_overrides[get_optional_current_user] = lambda: user


ROOT = {"id": str(uuid4()), "role": "super_admin", "email": "root@example.com"}


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


def body_id(resp):
    return resp.json()["id"]


def anonymous(client):
    client.app.dependency_overrides[get_current_user] = lambda: (_ for _ in ()).throw(AssertionError("auth required"))
    client.app.dependency_overrides[get_optional_catalog_user] = lambda: None
    client.app.dependency_overrides[get_optional_current_user] = lambda: None


def moderate(client, tid, review_id, action, user, prefix="trainings"):
    as_user(client, user)
    resp = client.patch(f"/api/v1/{prefix}/{tid}/reviews/{review_id}/moderate", json={"action": action, "reason": "Test reason"})
    as_user(client, LEARNER)
    return resp


# --- a new review is approved --------------------------------------------------------------------

def test_a_new_review_is_approved_and_public_at_once(world):
    client, _, tid = world

    resp = post(client, tid, rating=4)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["moderation_status"] == "approved"
    assert body["status_label"] == "Approved" and body["is_public"] is True and body["edit_label"] == "Save Changes"
    assert body["participant_name"] == "Asha Rao" and body["verified"] is True
    assert body["participant_email"] == LEARNER["email"]  # the author sees their own
    body = listing(client, tid)
    assert body["count"] == 1 and body["average_rating"] == 4.0
    assert [r["id"] for r in body["reviews"]] == [body_id(resp)]


def test_the_public_list_has_no_email_and_counts(world):
    client, _, tid = world
    review_id = post(client, tid, rating=4).json()["id"]

    body = listing(client, tid)
    assert body["count"] == 1 and body["average_rating"] == 4.0
    assert body["rating_distribution"] == {"5": 0, "4": 1, "3": 0, "2": 0, "1": 0}
    [item] = body["reviews"]
    assert item["participant_email"] is None and item["participant_name"] == "Asha Rao"
    assert item["moderation_status"] == "approved"
    anonymous(client)   # the author's own copy (my_review) carries their email; the public one never does
    assert LEARNER["email"] not in client.get(f"/api/v1/trainings/{tid}/reviews").text


def test_reject_and_reset_hide_it_again(world):
    client, _, tid = world
    review_id = post(client, tid).json()["id"]
    moderate(client, tid, review_id, "rejected", ROOT)
    assert listing(client, tid)["count"] == 0
    moderate(client, tid, review_id, "approved", ROOT)
    assert listing(client, tid)["count"] == 1
    moderate(client, tid, review_id, "pending", ROOT)
    assert listing(client, tid)["average_rating"] is None


def test_the_average_counts_approved_reviews_only(world):
    client, sessions, tid = world
    post(client, tid, rating=5)
    with sessions() as db:
        db.add(TrainingEnrolment(training_id=tid, participant_name="Ravi", participant_email="ravi@example.com", status="enrolled"))
        db.commit()
    as_user(client, {"id": str(uuid4()), "role": "customer", "email": "ravi@example.com"})
    second = post(client, tid, rating=1).json()["id"]
    as_user(client, LEARNER)
    moderate(client, tid, second, "pending", ROOT)  # a Super Admin holds it back

    body = listing(client, tid)

    assert body["count"] == 1 and body["average_rating"] == 5.0


def test_the_training_detail_and_cards_use_approved_reviews_only(world):
    client, _, tid = world
    review_id = post(client, tid, rating=3).json()["id"]
    moderate(client, tid, review_id, "pending", ROOT)
    detail = client.get(f"/api/v1/trainings/{tid}").json()
    assert detail["average_rating"] is None and detail["reviews_count"] == 0 and detail["reviews"] == []

    moderate(client, tid, review_id, "approved", ROOT)
    detail = client.get(f"/api/v1/trainings/{tid}").json()
    assert detail["average_rating"] == 3.0 and detail["reviews_count"] == 1
    assert "participant_email" not in detail["reviews"][0] and detail["reviews"][0]["participant_name"] == "Asha Rao"


# --- edit, limits, eligibility -------------------------------------------------------------------

def test_editing_updates_the_same_review_and_keeps_its_status(world):
    client, sessions, tid = world
    review_id = post(client, tid, rating=5, comment="first").json()["id"]

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

    # only a Super Admin moderates: not a provider, not the owning business's Enterprise Admin, not another business's
    for user in (staff("provider"), staff("admin"), staff("admin", OTHER_TENANT), LEARNER):
        resp = moderate(client, tid, review_id, "rejected", user)
        assert resp.status_code == 403, user
    assert resp.json()["detail"] == "Only a Super Admin can moderate reviews"
    assert listing(client, tid)["count"] == 1


def test_a_super_admin_can_moderate_any_business(world):
    client, _, tid = world
    review_id = post(client, tid).json()["id"]

    assert moderate(client, tid, review_id, "rejected", ROOT).status_code == 200
    assert listing(client, tid)["count"] == 0


def test_moderate_checks_the_review_belongs_to_the_training(world):
    client, sessions, tid = world
    other = uuid4()
    with sessions() as db:
        db.add(Training(id=other, enterprise_id=uuid4(), tenant_id=TENANT, title="Other", category="x", status="published",
                        delivery_mode="online", sections=[], assessments=[], assignments=[]))
        db.commit()
    review_id = post(client, tid).json()["id"]

    assert moderate(client, other, review_id, "approved", ROOT).status_code == 404
    assert moderate(client, tid, str(uuid4()), "approved", ROOT).status_code == 404
    assert moderate(client, tid, review_id, "published", ROOT).status_code == 400
    assert moderate(client, tid, review_id, "rejected", staff()).status_code == 403


def test_the_course_paths_are_the_same_reviews(world):
    client, _, tid = world
    review_id = post(client, tid, prefix="courses").json()["id"]
    assert listing(client, tid, prefix="trainings")["count"] == 1
    assert listing(client, tid, prefix="courses")["count"] == 1

    assert moderate(client, tid, review_id, "rejected", ROOT, prefix="courses").status_code == 200
    assert listing(client, tid, prefix="trainings")["count"] == 0
    assert listing(client, tid, prefix="courses")["count"] == 0


# --- my review -----------------------------------------------------------------------------------

def test_my_review_is_null_until_written_then_shows_the_status(world):
    client, _, tid = world
    assert client.get(f"/api/v1/trainings/{tid}/reviews/me").json() is None

    post(client, tid, rating=4)
    mine = client.get(f"/api/v1/trainings/{tid}/reviews/me").json()

    assert mine["rating"] == 4 and mine["moderation_status"] == "approved"
    assert mine["participant_email"] == LEARNER["email"]


# --- the author still sees a pending review in the listing; nobody else does -----------------------

def test_the_author_sees_their_pending_review_as_my_review_and_it_is_not_counted(world):
    client, sessions, tid = world
    resp = post(client, tid, rating=4)
    moderate(client, tid, resp.json()["id"], "pending", ROOT)
    with sessions() as db:
        assert db.query(TrainingReview).filter_by(id=UUID(resp.json()["id"])).one().moderation_status == "pending"

    body = listing(client, tid)

    assert body["my_review"]["id"] == resp.json()["id"]            # same id as the POST
    assert body["my_review"]["moderation_status"] == "pending"
    assert body["my_review"]["status_label"] == "Waiting for Approval" and body["my_review"]["is_public"] is False
    assert body["reviews"] == [] and body["count"] == 0 and body["average_rating"] is None
    assert body["pagination"]["total"] == 0


def test_an_unrelated_user_and_the_public_never_see_a_pending_review(world):
    client, sessions, tid = world
    moderate(client, tid, post(client, tid).json()["id"], "pending", ROOT)
    with sessions() as db:
        db.add(TrainingEnrolment(training_id=tid, participant_name="Ravi", participant_email="ravi@example.com", status="enrolled"))
        db.commit()

    as_user(client, {"id": str(uuid4()), "role": "customer", "email": "ravi@example.com"})
    other = listing(client, tid)
    anonymous(client)
    public = listing(client, tid)

    for body in (other, public):
        assert body["my_review"] is None and body["reviews"] == [] and body["count"] == 0
        assert LEARNER["email"] not in str(body)
    as_user(client, {"id": str(uuid4()), "role": "customer", "email": "ravi@example.com"})
    assert client.get(f"/api/v1/trainings/{tid}/reviews/me").json() is None


def test_my_review_follows_moderation_and_the_author_sees_a_rejected_one(world):
    client, _, tid = world
    review_id = post(client, tid).json()["id"]

    moderate(client, tid, review_id, "rejected", ROOT)
    body = listing(client, tid)
    assert body["my_review"]["moderation_status"] == "rejected" and body["reviews"] == [] and body["count"] == 0
    anonymous(client)
    assert listing(client, tid)["my_review"] is None

    as_user(client, LEARNER)
    moderate(client, tid, review_id, "approved", ROOT)
    body = listing(client, tid)
    assert body["my_review"]["moderation_status"] == "approved"
    assert [r["id"] for r in body["reviews"]] == [review_id] and body["count"] == 1
    anonymous(client)
    public = listing(client, tid)
    assert [r["id"] for r in public["reviews"]] == [review_id] and public["my_review"] is None


def test_ownership_cannot_be_forged_with_a_participant_email(world):
    client, sessions, tid = world
    with sessions() as db:
        db.add(TrainingEnrolment(training_id=tid, participant_name="Ravi", participant_email="ravi@example.com", status="enrolled"))
        db.commit()
    ravi = {"id": str(uuid4()), "role": "customer", "email": "ravi@example.com"}
    as_user(client, ravi)

    resp = client.post(f"/api/v1/trainings/{tid}/reviews", json={"rating": 1, "participant_email": LEARNER["email"]})

    assert resp.json()["participant_email"] == ravi["email"]       # the token's email wins
    as_user(client, LEARNER)
    assert listing(client, tid)["my_review"] is None                # nothing was written as the learner
    with sessions() as db:
        assert [r.participant_email for r in db.query(TrainingReview).all()] == [ravi["email"]]


def test_a_stale_token_on_the_public_listing_is_just_anonymous(world):
    client, _, tid = world
    post(client, tid)
    anonymous(client)
    assert listing(client, tid)["my_review"] is None


def test_course_paths_expose_my_review_the_same_way(world):
    client, _, tid = world
    review_id = post(client, tid, prefix="courses").json()["id"]
    assert listing(client, tid, prefix="courses")["my_review"]["id"] == review_id
    assert listing(client, tid, prefix="courses")["count"] == 1


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

    moderate(client, tid, review_id, "pending", ROOT)    # held back: not announced
    moderate(client, tid, review_id, "approved", ROOT)
    moderate(client, tid, review_id, "approved", ROOT)   # same status again: nothing new to say
    moderate(client, tid, review_id, "rejected", ROOT)

    # the author is told about real approve / reject decisions only (a moderator action also queues a separate
    # notice for the Super Admins; that is covered in test_review_and_discussion_notifications.py)
    assert [(name, args[2]) for name, args in dispatched if name == "_deliver_decision"] == [("_deliver_decision", "approved"), ("_deliver_decision", "rejected")]


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


def test_my_review_and_reviews_me_agree_through_every_status(world):
    client, _, tid = world
    review_id = post(client, tid).json()["id"]
    for action in ("pending", "rejected", "approved"):
        moderate(client, tid, review_id, action, ROOT)
        mine, listed = client.get(f"/api/v1/trainings/{tid}/reviews/me").json(), listing(client, tid)["my_review"]
        assert mine == listed and mine["id"] == review_id and mine["moderation_status"] == action


# --- edit, resubmit and the customer's labels -----------------------------------------------------------------

def test_a_pending_review_stays_pending_when_edited(world):
    client, _, tid = world
    review_id = post(client, tid, rating=5).json()["id"]
    moderate(client, tid, review_id, "pending", ROOT)

    edited = post(client, tid, rating=3, comment="updated")

    assert edited.json()["id"] == review_id and edited.json()["moderation_status"] == "pending"
    assert edited.json()["status_label"] == "Waiting for Approval" and edited.json()["edit_label"] == "Save Changes"
    assert listing(client, tid)["count"] == 0


def test_editing_a_rejected_review_resubmits_it_for_approval(world, dispatched):
    client, _, tid = world
    review_id = post(client, tid, rating=2).json()["id"]
    moderate(client, tid, review_id, "rejected", ROOT)
    mine = client.get(f"/api/v1/trainings/{tid}/reviews/me").json()
    assert mine["status_label"] == "Not Approved" and mine["edit_label"] == "Resubmit Review" and mine["is_public"] is False
    dispatched.clear()

    again = post(client, tid, rating=4, comment="the setup is clearer now")

    assert again.status_code == 201 and again.json()["id"] == review_id
    assert again.json()["moderation_status"] == "pending" and again.json()["status_label"] == "Waiting for Approval"
    assert listing(client, tid)["count"] == 0                       # hidden until a Super Admin decides
    assert [(n, a) for n, a in dispatched if n == "_deliver_submitted"] == [("_deliver_submitted", ("training", review_id, None, True))]

    assert moderate(client, tid, review_id, "approved", ROOT).status_code == 200
    assert listing(client, tid)["count"] == 1 and listing(client, tid)["average_rating"] == 4.0


def test_a_super_admin_can_reject_a_resubmitted_review_and_the_customer_can_resubmit_again(world):
    client, _, tid = world
    review_id = post(client, tid).json()["id"]
    moderate(client, tid, review_id, "rejected", ROOT)
    post(client, tid, rating=3)                                       # resubmitted -> pending

    assert moderate(client, tid, review_id, "rejected", ROOT).status_code == 200
    assert post(client, tid, rating=4).json()["moderation_status"] == "pending"


def test_a_rejected_review_cannot_be_put_back_to_pending_by_a_super_admin(world):
    client, _, tid = world
    review_id = post(client, tid).json()["id"]
    moderate(client, tid, review_id, "rejected", ROOT)

    resp = moderate(client, tid, review_id, "pending", ROOT)

    assert resp.status_code == 409 and resp.json()["detail"] == "A rejected review can only be moved to approved"


def test_the_author_can_delete_their_review_in_any_status_and_it_leaves_the_list(world):
    client, _, tid = world
    review_id = post(client, tid).json()["id"]
    moderate(client, tid, review_id, "rejected", ROOT)

    resp = client.request("DELETE", f"/api/v1/trainings/{tid}/reviews/{review_id}", json={"reason": "Test reason"})

    assert resp.status_code == 200 and resp.json() == {"message": "Review deleted"}
    assert client.get(f"/api/v1/trainings/{tid}/reviews/me").json() is None
    assert post(client, tid, rating=5).json()["moderation_status"] == "approved"   # a fresh review starts approved again


# --- a review a Super Admin deleted stays visible to its author, marked deleted ----------------------------------

def delete_as(client, tid, review_id, user):
    as_user(client, user)
    resp = client.request("DELETE", f"/api/v1/trainings/{tid}/reviews/{review_id}", json={"reason": "Test reason"})
    as_user(client, LEARNER)
    return resp


def test_the_author_still_sees_a_review_a_super_admin_deleted_with_the_status_deleted(world):
    client, sessions, tid = world
    review_id = post(client, tid, rating=4).json()["id"]

    assert delete_as(client, tid, review_id, ROOT).status_code == 200

    mine = listing(client, tid)["my_review"]
    assert mine["id"] == review_id and mine["moderation_status"] == "deleted"
    assert mine["status_label"] == "Deleted" and mine["is_public"] is False and mine["edit_label"] == "Resubmit Review"
    assert client.get(f"/api/v1/trainings/{tid}/reviews/me").json() == mine
    with sessions() as db:
        assert db.query(TrainingReview).count() == 1       # kept, not erased


def test_a_deleted_review_is_not_public_and_does_not_count(world):
    client, _, tid = world
    review_id = post(client, tid, rating=4).json()["id"]
    assert listing(client, tid)["count"] == 1

    delete_as(client, tid, review_id, ROOT)

    body = listing(client, tid)
    assert body["reviews"] == [] and body["count"] == 0 and body["average_rating"] is None
    assert client.get(f"/api/v1/trainings/{tid}").json()["reviews_count"] == 0
    anonymous(client)
    assert listing(client, tid)["my_review"] is None and listing(client, tid)["count"] == 0


def test_only_the_author_sees_the_deleted_review(world, ):
    client, sessions, tid = world
    review_id = post(client, tid).json()["id"]
    delete_as(client, tid, review_id, ROOT)
    with sessions() as db:
        db.add(TrainingEnrolment(training_id=tid, participant_name="Ravi", participant_email="ravi@example.com", status="enrolled"))
        db.commit()

    as_user(client, {"id": str(uuid4()), "role": "customer", "email": "ravi@example.com"})

    assert listing(client, tid)["my_review"] is None and client.get(f"/api/v1/trainings/{tid}/reviews/me").json() is None


def test_deleting_it_again_changes_nothing(world):
    client, _, tid = world
    review_id = post(client, tid).json()["id"]
    delete_as(client, tid, review_id, ROOT)

    again = delete_as(client, tid, review_id, ROOT)

    assert again.status_code == 200 and listing(client, tid)["my_review"]["moderation_status"] == "deleted"


def test_a_deleted_review_cannot_be_moderated(world):
    client, _, tid = world
    review_id = post(client, tid).json()["id"]
    delete_as(client, tid, review_id, ROOT)

    for action in ("approved", "rejected", "pending"):
        resp = moderate(client, tid, review_id, action, ROOT)
        assert resp.status_code == 409 and "deleted review cannot be moderated" in resp.json()["detail"]


def test_the_author_can_resubmit_a_deleted_review(world, dispatched):
    client, _, tid = world
    review_id = post(client, tid, rating=1, comment="rude").json()["id"]
    delete_as(client, tid, review_id, ROOT)
    dispatched.clear()

    again = post(client, tid, rating=3, comment="Fair course")

    assert again.json()["id"] == review_id and again.json()["moderation_status"] == "pending"
    assert again.json()["status_label"] == "Waiting for Approval"
    assert listing(client, tid)["count"] == 0
    assert [(n, a) for n, a in dispatched if n == "_deliver_submitted"] == [("_deliver_submitted", ("training", review_id, None, True))]
    assert moderate(client, tid, review_id, "approved", ROOT).status_code == 200
    assert listing(client, tid)["count"] == 1


def test_the_author_can_remove_the_deleted_review_for_good(world):
    client, sessions, tid = world
    review_id = post(client, tid).json()["id"]
    delete_as(client, tid, review_id, ROOT)

    resp = client.request("DELETE", f"/api/v1/trainings/{tid}/reviews/{review_id}", json={"reason": "Test reason"})

    assert resp.status_code == 200
    assert listing(client, tid)["my_review"] is None
    with sessions() as db:
        assert db.query(TrainingReview).count() == 0


def test_staff_still_cannot_delete_it(world):
    client, _, tid = world
    review_id = post(client, tid).json()["id"]

    assert delete_as(client, tid, review_id, staff()).status_code == 403
    assert listing(client, tid)["my_review"]["moderation_status"] == "approved"
