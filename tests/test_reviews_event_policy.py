"""Event reviews: the reviewer is the logged-in user, one review per person, validated input, moderation by the
owning Enterprise Admin or a Super Admin, and notifications."""
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
    return client_for(world.db, user).patch(url(world, f"/{review_id}/moderate"), json={"action": action})


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
    assert moderate(world, review_id, "approved", staff_user(TENANT, "admin")).status_code == 200

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

    assert mine["rating"] == "4" and mine["moderation_status"] == "pending"
    assert client_for(world.db, customer_user("other@example.com")).get(url(world, "/me")).json() is None


# --- moderation ----------------------------------------------------------------------------------

def test_the_owning_admin_and_a_super_admin_can_moderate_but_not_providers_or_other_businesses(world):
    review_id = write(world).json()["id"]

    for user in (staff_user(TENANT, "provider"), staff_user(OTHER, "admin"), world.asha):
        assert moderate(world, review_id, "approved", user).status_code == 403, user
    assert moderate(world, review_id, "approved", staff_user(TENANT, "admin")).status_code == 200
    assert moderate(world, review_id, "rejected", super_admin_user()).status_code == 200
    assert moderate(world, review_id, "bogus", staff_user(TENANT, "admin")).status_code == 400


def test_a_super_admin_can_open_the_staff_list(world):
    write(world)

    resp = client_for(world.db, super_admin_user()).get(url(world, "/manage"), params={"status": "pending"})

    assert resp.status_code == 200 and len(resp.json()["reviews"]) == 1


def test_a_feedback_row_cannot_be_moderated_as_a_review(world):
    row = EventFeedback(event_id=world.event.id, participant_email="x@example.com", is_review=False)
    world.db.add(row)
    world.db.commit()

    assert moderate(world, row.id, "approved", staff_user(TENANT, "admin")).status_code == 404


def test_the_public_list_shows_the_star_breakdown(world):
    review_id = write(world, rating=4).json()["id"]
    moderate(world, review_id, "approved", staff_user(TENANT, "admin"))

    body = client_for(world.db, None).get(url(world)).json()

    assert body["rating_distribution"] == {"5": 0, "4": 1, "3": 0, "2": 0, "1": 0}


# --- notifications -------------------------------------------------------------------------------

def test_notified_on_submit_and_on_real_decisions_only(world, dispatched):
    review_id = write(world).json()["id"]
    write(world, rating=1)
    assert [(n, a[0]) for n, a in dispatched] == [("_deliver_submitted", "event")]
    dispatched.clear()

    admin = staff_user(TENANT, "admin")
    for action in ("approved", "approved", "pending", "rejected"):
        moderate(world, review_id, action, admin)

    assert [(n, a[2]) for n, a in dispatched] == [("_deliver_decision", "approved"), ("_deliver_decision", "rejected")]


# --- the feedback report -------------------------------------------------------------------------

def test_the_feedback_report_averages_approved_reviews_only(world):
    admin = staff_user(TENANT, "admin")
    review_id = write(world, rating=4).json()["id"]

    def report():
        return client_for(world.db, admin).get(f"{API}/{world.event.id}/reports", params={"type": "feedback"}).json()["data"]

    pending = report()
    assert pending["total_reviews"] == 1 and pending["approved_reviews"] == 0 and pending["average_rating"] is None

    moderate(world, review_id, "approved", admin)
    approved = report()
    assert approved["approved_reviews"] == 1 and approved["average_rating"] == 4.0

    moderate(world, review_id, "rejected", admin)
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
