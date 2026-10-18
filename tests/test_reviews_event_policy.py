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
    assert [(n, a[2]) for n, a in dispatched if n == "_deliver_decision"] == [
        ("_deliver_decision", "pending"), ("_deliver_decision", "approved"), ("_deliver_decision", "rejected"),
    ]


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


# --- my_review in the public list, and the numbers on the events list -------------------------------------------------

def second_reviewer(world, rating=3, comment="Fine"):
    ravi = customer_user("ravi@example.com")
    make_registration(world.db, world.event, "ravi@example.com")
    resp = write(world, ravi, rating=rating, comment=comment)
    assert resp.status_code == 201, resp.text
    return ravi, resp.json()["id"]


def public_list(world, user=None, **params):
    return client_for(world.db, user).get(url(world), params=params).json()


def test_my_review_is_null_for_an_anonymous_reader_and_for_someone_with_no_review(world):
    write(world, rating=5)

    assert public_list(world)["my_review"] is None
    assert public_list(world, customer_user("other@example.com"))["my_review"] is None


def test_the_logged_in_users_review_is_my_review_and_not_in_the_reviews_list(world):
    mine = write(world, rating=5, comment="Good").json()["id"]
    ravi, theirs = second_reviewer(world, rating=3, comment="Fine")

    body = public_list(world, world.asha)

    assert body["my_review"]["id"] == mine
    assert body["my_review"]["rating"] == 5 and body["my_review"]["comment"] == "Good"
    assert body["my_review"]["status"] == "approved" and body["my_review"]["moderation_status"] == "approved"
    assert body["my_review"]["status_label"] == "Approved" and body["my_review"]["is_public"] is True
    assert body["my_review"]["edit_label"] == "Save Changes" and body["my_review"]["participant_name"]
    assert "participant_email" not in body["my_review"] and "@" not in str(body["my_review"])
    assert [r["id"] for r in body["reviews"]] == [theirs]                                  # only the other person's
    # everything else still describes all the approved reviews, mine included
    assert body["count"] == 2 and body["average_rating"] == 4.0
    assert body["rating_distribution"] == {"5": 1, "4": 0, "3": 1, "2": 0, "1": 0}
    # the paging describes the `reviews` list
    assert body["pagination"] == {"total": 1, "page": 1, "page_size": 1, "total_pages": 1}


def test_each_user_sees_their_own_review_as_my_review(world):
    mine = write(world, rating=5).json()["id"]
    ravi, theirs = second_reviewer(world)

    as_ravi = public_list(world, ravi)
    anonymous = public_list(world)

    assert as_ravi["my_review"]["id"] == theirs and [r["id"] for r in as_ravi["reviews"]] == [mine]
    assert anonymous["my_review"] is None and {r["id"] for r in anonymous["reviews"]} == {mine, theirs}   # no one is left out


@pytest.mark.parametrize("action,label", [("pending", "Waiting for Approval"), ("rejected", "Not Approved")])
def test_my_review_shows_a_review_that_is_not_public_with_its_status(world, action, label):
    mine = write(world, rating=2).json()["id"]
    _, theirs = second_reviewer(world, rating=4)
    moderate(world, mine, action, super_admin_user())

    body = public_list(world, world.asha)

    assert body["my_review"]["id"] == mine and body["my_review"]["status"] == action
    assert body["my_review"]["status_label"] == label and body["my_review"]["is_public"] is False
    assert [r["id"] for r in body["reviews"]] == [theirs]
    assert body["count"] == 1 and body["average_rating"] == 4.0                            # the hidden one does not count


def test_my_review_shows_a_review_a_super_admin_deleted_as_deleted(world):
    mine = write(world, rating=2).json()["id"]
    client_for(world.db, super_admin_user()).request("DELETE", url(world, f"/{mine}"), json={"reason": "Abusive language"})

    body = public_list(world, world.asha)

    assert body["my_review"]["status"] == "deleted" and body["my_review"]["status_label"] == "Deleted"
    assert body["my_review"]["edit_label"] == "Resubmit Review" and body["reviews"] == [] and body["count"] == 0


def test_my_review_is_found_by_user_id_and_an_old_review_by_email(world):
    old = EventFeedback(event_id=world.event.id, participant_email="Asha@example.com", rating="4", comment="Old", is_review=True, moderation_status="approved")
    world.db.add(old)
    world.db.commit()

    body = public_list(world, world.asha)

    assert body["my_review"]["id"] == str(old.id) and body["reviews"] == [] and body["count"] == 1


def test_my_review_is_not_changed_by_the_filters_or_the_paging(world):
    write(world, rating=5)
    second_reviewer(world, rating=1, comment=None)

    body = public_list(world, world.asha, rating=1, page=1, page_size=1)

    assert body["my_review"]["rating"] == 5                                                # whatever the filter
    assert [r["rating"] for r in body["reviews"]] == [1] and body["count"] == 2


def listing(world, user=None):
    return client_for(world.db, user).get(f"{API}/", params={"status": "published", "page": 1, "page_size": 50}).json()


def test_the_events_list_has_approved_reviews_and_average_rating_on_every_item(world):
    other = make_event(world.db, TENANT, enterprise=world.enterprise, title="Quiet event")

    before = {i["id"]: i for i in listing(world)["items"]}
    assert before[str(world.event.id)]["approved_reviews"] == 0 and before[str(world.event.id)]["average_rating"] is None

    write(world, rating=5)
    second_reviewer(world, rating=4)
    hidden = second_reviewer_hidden(world)
    after = {i["id"]: i for i in listing(world)["items"]}

    assert after[str(world.event.id)]["approved_reviews"] == 2 and after[str(world.event.id)]["average_rating"] == 4.5
    assert after[str(other.id)]["approved_reviews"] == 0 and after[str(other.id)]["average_rating"] is None
    assert hidden                                                                         # a rejected review never counts


def second_reviewer_hidden(world):
    nair = customer_user("nair@example.com")
    make_registration(world.db, world.event, "nair@example.com")
    review_id = write(world, nair, rating=1).json()["id"]
    moderate(world, review_id, "rejected", super_admin_user())
    return review_id


def test_the_event_list_numbers_come_from_one_query_for_the_page(world):
    from sqlalchemy import event as sa_event

    for i in range(4):
        make_event(world.db, TENANT, enterprise=world.enterprise, title=f"Event {i}")
    statements = []
    sa_event.listen(world.db.bind, "before_cursor_execute", lambda *a: statements.append(a[2]))

    listing(world)

    assert sum(1 for s in statements if "event_feedback" in s and "FROM event_feedback" in s and "moderation_status" in s) <= 2


# --- the author reads the Super Admin's reason on their own review --------------------------------------------------

def moderate_reason(world, review_id, action, reason):
    return client_for(world.db, super_admin_user()).patch(url(world, f"/{review_id}/moderate"), json={"action": action, "reason": reason})


def test_my_review_carries_the_reason_for_a_hold_a_rejection_and_a_deletion(world):
    review_id = write(world, rating=3).json()["id"]
    assert public_list(world, world.asha)["my_review"]["reason"] is None

    moderate_reason(world, review_id, "pending", "Needs a second look")
    mine = public_list(world, world.asha)["my_review"]
    assert mine["status"] == "pending" and mine["reason"] == "Needs a second look"

    moderate_reason(world, review_id, "rejected", "Not about this event")
    mine = client_for(world.db, world.asha).get(url(world, "/me")).json()
    assert mine["status"] == "rejected" and mine["reason"] == "Not about this event" and mine["status_label"] == "Not Approved"

    client_for(world.db, super_admin_user()).request("DELETE", url(world, f"/{review_id}"), json={"reason": "Abusive language"})
    mine = public_list(world, world.asha)["my_review"]
    assert mine["status"] == "deleted" and mine["reason"] == "Abusive language"
    assert "Abusive language" not in str(public_list(world))                                    # not in the public list


def test_the_event_reason_goes_when_the_author_resubmits_or_it_is_approved(world):
    review_id = write(world, rating=3).json()["id"]
    moderate_reason(world, review_id, "rejected", "Not about this event")

    assert write(world, rating=4, comment="About the event now").json()["reason"] is None
    moderate_reason(world, review_id, "approved", "n/a")
    moderate_reason(world, review_id, "pending", "Checking")
    assert public_list(world, world.asha)["my_review"]["reason"] == "Checking"
    assert write(world, rating=5, comment="Edit while held back").json()["reason"] == "Checking"   # still pending: kept
    moderate_reason(world, review_id, "approved", "n/a")
    assert public_list(world, world.asha)["my_review"]["reason"] is None


def test_the_staff_list_and_the_all_module_list_show_the_reason(world):
    review_id = write(world, rating=3).json()["id"]
    moderate_reason(world, review_id, "pending", "Needs a second look")

    staff = client_for(world.db, staff_user(TENANT, "admin")).get(url(world, "/manage")).json()

    assert staff["reviews"][0]["reason"] == "Needs a second look" and staff["reviews"][0]["moderation_status"] == "pending"
