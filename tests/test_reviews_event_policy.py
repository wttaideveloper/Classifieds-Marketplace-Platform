"""Event reviews: the reviewer is the logged-in user, one review per person, validated input, a new review is
approved at once, only a Super Admin moderates, and notifications."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.event_aux_models import EventFeedback
from app.services import review_notifications
from tests.event_sql_support import (
    API, client_for, customer_user, make_enterprise, make_event, make_registration, make_session, reset_overrides,
    staff_user, super_admin_user,
)

TENANT, OTHER = uuid4(), uuid4()


@pytest.fixture
def dispatched(monkeypatch):
    calls = []
    monkeypatch.setattr(review_notifications, "_dispatch", lambda fn, *a, **k: calls.append((fn.__name__, a)))
    return calls


@pytest.fixture
def world(dispatched):
    db = make_session()
    enterprise = make_enterprise(db, TENANT)
    event = make_event(db, TENANT, enterprise=enterprise)
    asha = customer_user("asha@example.com")
    make_registration(db, event, "asha@example.com")
    yield SimpleNamespace(db=db, event=event, asha=asha, enterprise=enterprise)
    reset_overrides()
    db.close()


def url(world, tail=""):
    return f"{API}/{world.event.id}/reviews{tail}"


def write(world, user=None, **body):
    body.setdefault("rating", 5)
    return client_for(world.db, user or world.asha).post(url(world), json=body)


def moderate(world, review_id, action, user):
    return client_for(world.db, user).patch(url(world, f"/{review_id}/moderate"), json={"action": action, "reason": "Test reason"})


# --- who the reviewer is -------------------------------------------------------------------------

def test_the_reviewer_is_the_logged_in_user_not_the_email_in_the_body(world):
    # Ravi is registered; Asha is logged in. Asha cannot review as Ravi.
    make_registration(world.db, world.event, "ravi@example.com")
    resp = write(world, participant_email="ravi@example.com", comment="as Ravi")

    assert resp.status_code == 201, resp.text
    assert resp.json()["participant_email"] == "asha@example.com"
    assert "user_id" not in resp.json()
    assert world.db.query(EventFeedback).filter_by(participant_email="ravi@example.com").count() == 0


def test_a_logged_in_user_who_is_not_registered_cannot_review_even_with_a_registered_email_in_the_body(world):
    stranger = customer_user("stranger@example.com")

    resp = write(world, stranger, participant_email="asha@example.com")

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Only registered participants can submit verified reviews"


@pytest.mark.parametrize("status", ["cancelled", "no_show", "pending"])
def test_only_confirmed_or_attended_registrations_can_review(world, status):
    world.db.query(__import__("app.models.event_aux_models", fromlist=["EventRegistration"]).EventRegistration).update({"status": status})
    world.db.commit()

    assert write(world).status_code == 403


def test_attended_registrations_can_review_and_email_case_does_not_matter(world):
    from app.models.event_aux_models import EventRegistration

    world.db.query(EventRegistration).update({"status": "attended"})
    world.db.commit()
    shouting = {**world.asha, "email": "Asha@Example.com"}

    assert write(world, shouting).status_code == 201


def test_a_token_without_an_email_cannot_review(world):
    assert write(world, {"id": str(uuid4()), "role": "customer"}).status_code == 403


# --- validation ----------------------------------------------------------------------------------

@pytest.mark.parametrize("rating", [None, 0, 6, -1, "abc", "4.5", True, ""])
def test_a_missing_or_invalid_rating_is_422(world, rating):
    body = {} if rating is None else {"rating": rating}
    resp = client_for(world.db, world.asha).post(url(world), json=body)
    assert resp.status_code == 422, (rating, resp.text)


@pytest.mark.parametrize("rating", [1, 5, "3"])
def test_a_whole_number_rating_is_accepted_as_a_number_or_as_text(world, rating):
    resp = write(world, rating=rating)
    assert resp.status_code == 201 and resp.json()["rating"] == str(rating)


def test_the_comment_limit_is_2000_characters(world):
    assert write(world, comment="x" * 2001).status_code == 422
    assert write(world, comment="x" * 2000).status_code == 201
    assert write(world, comment="   ").json()["comment"] is None


# --- one review per person -----------------------------------------------------------------------

def test_a_second_post_updates_the_review_and_keeps_its_status(world):
    review_id = write(world, rating=5, comment="first").json()["id"]

    again = write(world, rating=2, comment="second")

    assert again.status_code == 201 and again.json()["id"] == review_id
    assert again.json()["rating"] == "2" and again.json()["moderation_status"] == "approved"
    assert world.db.query(EventFeedback).filter_by(is_review=True).count() == 1


def test_a_review_saved_before_user_ids_existed_is_found_by_email_and_updated(world):
    old = EventFeedback(event_id=world.event.id, participant_email="Asha@example.com", rating="3", is_review=True, moderation_status="approved")
    world.db.add(old)
    world.db.commit()

    resp = write(world, rating=4)

    assert resp.json()["id"] == str(old.id) and resp.json()["moderation_status"] == "approved"
    assert world.db.query(EventFeedback).filter_by(is_review=True).count() == 1
    world.db.refresh(old)
    assert old.user_id is not None  # remembered from now on


def test_feedback_forms_are_unaffected(world):
    client = client_for(world.db, world.asha)
    for _ in range(2):
        assert client.post(f"{API}/{world.event.id}/feedback", json={"participant_email": "x@example.com", "rating": "9"}).status_code == 201
    assert world.db.query(EventFeedback).filter_by(is_review=False).count() == 2


# --- my review -----------------------------------------------------------------------------------

def test_my_review_is_null_then_shows_the_status(world):
    client = client_for(world.db, world.asha)
    assert client.get(url(world, "/me")).json() is None

    write(world, rating=4)
    mine = client.get(url(world, "/me")).json()

    assert mine["rating"] == "4" and mine["moderation_status"] == "approved"
    assert mine["status_label"] == "Approved" and mine["is_public"] is True and mine["edit_label"] == "Save Changes"
    assert client_for(world.db, customer_user("other@example.com")).get(url(world, "/me")).json() is None


# --- moderation ----------------------------------------------------------------------------------

def test_only_a_super_admin_can_moderate(world):
    review_id = write(world).json()["id"]

    for user in (staff_user(TENANT, "provider"), staff_user(TENANT, "admin"), staff_user(OTHER, "admin"), world.asha):
        assert moderate(world, review_id, "rejected", user).status_code == 403, user
    assert moderate(world, review_id, "rejected", super_admin_user()).status_code == 200
    assert moderate(world, review_id, "bogus", super_admin_user()).status_code == 400
    assert moderate(world, review_id, "pending", super_admin_user()).status_code == 409   # rejected -> approved only


def test_editing_a_rejected_review_resubmits_it(world, dispatched):
    review_id = write(world, rating=3).json()["id"]
    moderate(world, review_id, "rejected", super_admin_user())
    mine = client_for(world.db, world.asha).get(url(world, "/me")).json()
    assert mine["status_label"] == "Not Approved" and mine["edit_label"] == "Resubmit Review"
    dispatched.clear()

    again = write(world, rating=4, comment="better")

    assert again.json()["id"] == review_id and again.json()["moderation_status"] == "pending"
    assert again.json()["status_label"] == "Waiting for Approval" and again.json()["is_public"] is False
    assert client_for(world.db, None).get(url(world)).json()["count"] == 0
    assert [(n, a) for n, a in dispatched if n == "_deliver_submitted"] == [("_deliver_submitted", ("event", review_id, None, True))]
    assert moderate(world, review_id, "approved", super_admin_user()).status_code == 200
    assert client_for(world.db, None).get(url(world)).json()["count"] == 1


def test_a_super_admin_can_open_the_staff_list(world):
    write(world)

    resp = client_for(world.db, super_admin_user()).get(url(world, "/manage"), params={"status": "approved"})

    assert resp.status_code == 200 and len(resp.json()["reviews"]) == 1


def test_a_feedback_row_cannot_be_moderated_as_a_review(world):
    row = EventFeedback(event_id=world.event.id, participant_email="x@example.com", is_review=False)
    world.db.add(row)
    world.db.commit()

    assert moderate(world, row.id, "rejected", super_admin_user()).status_code == 404


def test_the_public_list_shows_the_star_breakdown(world):
    write(world, rating=4)

    body = client_for(world.db, None).get(url(world)).json()

    assert body["rating_distribution"] == {"5": 0, "4": 1, "3": 0, "2": 0, "1": 0}


# --- notifications -------------------------------------------------------------------------------

def test_notified_on_submit_and_on_real_decisions_only(world, dispatched):
    review_id = write(world).json()["id"]
    write(world, rating=1)
    assert [(n, a[0]) for n, a in dispatched] == [("_deliver_submitted", "event")]
    dispatched.clear()

    root = super_admin_user()
    for action in ("pending", "approved", "approved", "rejected"):
        moderate(world, review_id, action, root)

    # the author is told about real approve / reject decisions only (a moderator action also queues a separate
    # notice for the Super Admins; that is covered in test_review_and_discussion_notifications.py)
    assert [(n, a[2]) for n, a in dispatched if n == "_deliver_decision"] == [("_deliver_decision", "approved"), ("_deliver_decision", "rejected")]


# --- the feedback report -------------------------------------------------------------------------

def test_the_feedback_report_averages_approved_reviews_only(world):
    admin, root = staff_user(TENANT, "admin"), super_admin_user()
    review_id = write(world, rating=4).json()["id"]

    def report():
        return client_for(world.db, admin).get(f"{API}/{world.event.id}/reports", params={"type": "feedback"}).json()["data"]

    approved = report()
    assert approved["total_reviews"] == 1 and approved["approved_reviews"] == 1 and approved["average_rating"] == 4.0

    moderate(world, review_id, "pending", root)
    pending = report()
    assert pending["approved_reviews"] == 0 and pending["average_rating"] is None

    moderate(world, review_id, "rejected", root)
    assert report()["average_rating"] is None


# --- sorting, filtering and paging the public list -----------------------------------------------

def seed_ordered(world):
    from datetime import datetime

    for minute, (rating, comment) in enumerate([(5, "five"), (3, "three-a"), (4, None), (3, "three-b"), (1, None)]):
        world.db.add(EventFeedback(
            event_id=world.event.id, participant_email=f"p{minute}@example.com", rating=str(rating), comment=comment,
            is_review=True, moderation_status="approved", created_at=datetime(2026, 1, 1, 0, minute),
        ))
    world.db.commit()


def public(world, **params):
    body = client_for(world.db, None).get(url(world), params=params).json()
    return [r["rating"] for r in body["reviews"]], body


def test_sorting_filtering_and_paging_do_not_change_the_summary(world):
    seed_ordered(world)

    assert public(world)[0] == [1, 3, 4, 3, 5]
    assert public(world, sort="oldest")[0] == [5, 3, 4, 3, 1]
    assert public(world, sort="highest")[0] == [5, 4, 3, 3, 1]
    assert public(world, sort="lowest")[0] == [1, 3, 3, 4, 5]
    assert client_for(world.db, None).get(url(world), params={"sort": "best"}).status_code == 422

    got, body = public(world, rating=3)
    assert got == [3, 3] and body["pagination"]["total"] == 2
    assert body["count"] == 5 and body["average_rating"] == 3.2
    assert public(world, with_comment="true")[0] == [3, 3, 5]

    assert public(world)[1]["pagination"] == {"total": 5, "page": 1, "page_size": 5, "total_pages": 1}
    got, body = public(world, page=2, page_size=2)
    assert got == [4, 3] and body["pagination"] == {"total": 5, "page": 2, "page_size": 2, "total_pages": 3}


# --- a review a Super Admin deleted stays visible to its author, marked deleted ----------------------------------

def delete_review(world, review_id, user):
    return client_for(world.db, user).request("DELETE", url(world, f"/{review_id}"), json={"reason": "Test reason"})


def test_a_review_a_super_admin_deleted_shows_as_deleted_to_its_author(world):
    review_id = write(world, rating=5).json()["id"]
    assert delete_review(world, review_id, super_admin_user()).status_code == 200

    mine = client_for(world.db, world.asha).get(url(world, "/me")).json()

    assert mine["id"] == review_id and mine["moderation_status"] == "deleted"
    assert mine["status_label"] == "Deleted" and mine["is_public"] is False and mine["edit_label"] == "Resubmit Review"
    public_list = client_for(world.db, None).get(url(world)).json()
    assert public_list["count"] == 0 and public_list["average_rating"] is None
    assert client_for(world.db, customer_user("other@example.com")).get(url(world, "/me")).json() is None


def test_a_deleted_event_review_is_in_no_staff_tab_and_not_in_the_report(world):
    review_id = write(world, rating=4).json()["id"]
    admin = staff_user(TENANT, "admin")
    delete_review(world, review_id, super_admin_user())

    staff_list = client_for(world.db, admin).get(url(world, "/manage")).json()
    report = client_for(world.db, admin).get(f"{API}/{world.event.id}/reports", params={"type": "feedback"}).json()["data"]

    assert staff_list["reviews"] == [] and staff_list["counts"] == {"pending": 0, "approved": 0, "rejected": 0}
    assert report["total_reviews"] == 0 and report["approved_reviews"] == 0 and report["average_rating"] is None


def test_a_deleted_event_review_cannot_be_moderated_and_the_author_can_resubmit_it(world, dispatched):
    review_id = write(world, rating=2).json()["id"]
    delete_review(world, review_id, super_admin_user())
    assert moderate(world, review_id, "approved", super_admin_user()).status_code == 409
    dispatched.clear()

    again = write(world, rating=4, comment="Better after all")

    assert again.json()["id"] == review_id and again.json()["moderation_status"] == "pending"
    assert [(n, a) for n, a in dispatched if n == "_deliver_submitted"] == [("_deliver_submitted", ("event", review_id, None, True))]
    assert moderate(world, review_id, "approved", super_admin_user()).status_code == 200
    assert client_for(world.db, None).get(url(world)).json()["count"] == 1


def test_the_author_removes_a_deleted_event_review_for_good_and_staff_still_cannot(world):
    review_id = write(world).json()["id"]
    assert delete_review(world, review_id, staff_user(TENANT, "admin")).status_code == 403
    delete_review(world, review_id, super_admin_user())

    assert delete_review(world, review_id, world.asha).status_code == 200
    assert client_for(world.db, world.asha).get(url(world, "/me")).json() is None
