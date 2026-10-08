"""Reading event reviews: the public list of approved reviews and the manager list of every review.

Before this, event reviews could be written (POST /events/{id}/reviews) and moderated, but nothing listed them.
"""
from uuid import uuid4

import pytest

from app.models.event_aux_models import EventFeedback
from tests.event_sql_support import (
    API, client_for, make_enterprise, make_event, make_registration, make_session, reset_overrides, staff_user,
    super_admin_user,
)

TENANT, OTHER_TENANT = uuid4(), uuid4()


@pytest.fixture
def world():
    db = make_session()
    enterprise = make_enterprise(db, TENANT)
    event = make_event(db, TENANT, enterprise=enterprise)
    owner = staff_user(TENANT, "admin")
    yield type("W", (), dict(db=db, enterprise=enterprise, event=event, owner=owner))
    reset_overrides()
    db.close()


def review(w, email, rating="5", comment="Loved it", moderation_status="approved", is_review=True, event=None):
    row = EventFeedback(
        event_id=(event or w.event).id, participant_email=email, rating=rating, comment=comment,
        is_review=is_review, moderation_status=moderation_status,
    )
    w.db.add(row)
    w.db.commit()
    return row


def public(w, event=None):
    return client_for(w.db, None).get(f"{API}/{(event or w.event).id}/reviews")


def manage(w, user=None, event=None, **params):
    return client_for(w.db, user or w.owner).get(f"{API}/{(event or w.event).id}/reviews/manage", params=params)


# --- public list --------------------------------------------------------------------------------------------

def test_public_list_returns_only_approved_reviews(world):
    make_registration(world.db, world.event, "asha@example.com")
    approved = review(world, "asha@example.com", "4", "Good")
    review(world, "p@example.com", "1", "pending one", moderation_status="pending")
    review(world, "r@example.com", "1", "rejected one", moderation_status="rejected")

    resp = public(world)

    assert resp.status_code == 200
    body = resp.json()
    assert [r["id"] for r in body["reviews"]] == [str(approved.id)]
    assert body["count"] == 1 and body["average_rating"] == 4.0


def test_public_list_never_contains_an_email_and_shows_the_registration_name(world):
    make_registration(world.db, world.event, "asha@example.com")
    review(world, "asha@example.com")

    [item] = public(world).json()["reviews"]

    assert item["participant_name"] == "asha"  # the name on the registration
    assert "participant_email" not in item and "@" not in str(item)


def test_a_name_that_is_an_email_is_not_returned(world):
    reg = make_registration(world.db, world.event, "asha@example.com")
    reg.participant_name = "asha@example.com"
    world.db.commit()
    review(world, "asha@example.com")

    [item] = public(world).json()["reviews"]

    assert item["participant_name"] is None


def test_average_is_null_when_nothing_is_approved(world):
    review(world, "p@example.com", moderation_status="pending")

    body = public(world).json()

    assert body == {
        "reviews": [], "average_rating": None, "count": 0,
        "rating_distribution": {"5": 0, "4": 0, "3": 0, "2": 0, "1": 0},
        "pagination": {"total": 0, "page": 1, "page_size": 0, "total_pages": 0},
    }


def test_average_ignores_ratings_that_are_not_1_to_5_and_is_rounded(world):
    review(world, "a@example.com", "5")
    review(world, "b@example.com", "4")
    review(world, "c@example.com", "4")
    review(world, "d@example.com", "11")  # unvalidated on the way in; must not count
    review(world, "e@example.com", "great")

    body = public(world).json()

    assert body["average_rating"] == 4.33
    assert body["count"] == 5
    assert sorted(r["rating"] for r in body["reviews"] if r["rating"] is not None) == [4, 4, 5]


def test_plain_feedback_is_not_listed_as_a_review(world):
    review(world, "a@example.com", "5", is_review=False)

    assert public(world).json()["count"] == 0


def test_newest_first(world):
    first = review(world, "a@example.com", "5", "first")
    second = review(world, "b@example.com", "4", "second")
    second.created_at = first.created_at.replace(year=first.created_at.year + 1)
    world.db.commit()

    assert [r["comment"] for r in public(world).json()["reviews"]] == ["second", "first"]


def test_reviews_of_an_unpublished_event_are_not_public(world):
    draft = make_event(world.db, TENANT, enterprise=world.enterprise, status="draft")
    review(world, "a@example.com", event=draft)

    assert public(world, draft).status_code == 404


def test_unknown_event_is_404(world):
    assert client_for(world.db, None).get(f"{API}/{uuid4()}/reviews").status_code == 404


def test_reviews_of_one_event_do_not_leak_into_another(world):
    other = make_event(world.db, TENANT, enterprise=world.enterprise)
    review(world, "a@example.com", event=other)

    assert public(world).json()["count"] == 0


# --- manager list -------------------------------------------------------------------------------------------

def test_manager_sees_every_status_with_counts_and_the_ids_to_moderate(world):
    pending = review(world, "p@example.com", "2", moderation_status="pending")
    review(world, "a@example.com", "5", moderation_status="approved")
    review(world, "r@example.com", "1", moderation_status="rejected")

    resp = manage(world)

    assert resp.status_code == 200
    body = resp.json()
    assert body["counts"] == {"pending": 1, "approved": 1, "rejected": 1}
    assert len(body["reviews"]) == 3
    assert {r["id"] for r in body["reviews"]} >= {str(pending.id)}
    assert body["average_rating"] == 5.0  # approved only
    assert body["pagination"]["total"] == 3


def test_manager_can_filter_by_status(world):
    pending = review(world, "p@example.com", moderation_status="pending")
    review(world, "a@example.com", moderation_status="approved")

    body = manage(world, status="pending").json()

    assert [r["id"] for r in body["reviews"]] == [str(pending.id)]
    assert body["reviews"][0]["moderation_status"] == "pending"
    assert body["reviews"][0]["participant_email"] == "p@example.com"
    assert body["counts"]["approved"] == 1  # the tab counts ignore the filter


def test_a_review_with_no_status_counts_as_pending(world):
    row = review(world, "p@example.com")
    row.moderation_status = None
    world.db.commit()

    body = manage(world, status="pending").json()

    assert [r["id"] for r in body["reviews"]] == [str(row.id)]
    assert body["counts"]["pending"] == 1


def test_bad_status_filter_is_400(world):
    assert manage(world, status="spam").status_code == 400


def test_manager_list_is_paged(world):
    for i in range(5):
        review(world, f"u{i}@example.com")

    body = manage(world, page=2, page_size=2).json()

    assert len(body["reviews"]) == 2 and body["pagination"]["total"] == 5


def test_manager_list_excludes_plain_feedback(world):
    review(world, "a@example.com", is_review=False)

    assert manage(world).json()["counts"] == {"pending": 0, "approved": 0, "rejected": 0}


def test_moderating_from_the_list_changes_what_the_public_sees(world):
    """The whole loop: write -> owner finds it in the list -> approves it -> it is public."""
    client = client_for(world.db, staff_user(uuid4(), "customer"))  # any logged-in user may post; role is irrelevant
    make_registration(world.db, world.event, "asha@example.com")
    client_for(world.db, {"id": str(uuid4()), "role": "customer", "email": "asha@example.com"}).post(
        f"{API}/{world.event.id}/reviews", json={"participant_email": "asha@example.com", "rating": "5", "comment": "Great"}
    )
    assert public(world).json()["count"] == 0

    [pending] = manage(world, status="pending").json()["reviews"]
    approve = client_for(world.db, world.owner).patch(
        f"{API}/{world.event.id}/reviews/{pending['id']}/moderate", json={"action": "approved"}
    )
    assert approve.status_code == 200

    body = public(world).json()
    assert body["count"] == 1 and body["average_rating"] == 5.0 and body["reviews"][0]["comment"] == "Great"


# --- who may use the manager list ---------------------------------------------------------------------------

def test_anonymous_is_refused(world):
    assert client_for(world.db, None).get(f"{API}/{world.event.id}/reviews/manage").status_code in (401, 403)


def test_customer_is_refused(world):
    customer = {"id": str(uuid4()), "role": "customer", "email": "c@example.com"}
    assert manage(world, user=customer).status_code == 403


def test_another_tenants_admin_is_refused(world):
    assert manage(world, user=staff_user(OTHER_TENANT, "admin")).status_code == 403


def test_provider_of_the_owning_tenant_can_read(world):
    assert manage(world, user=staff_user(TENANT, "provider")).status_code == 200


# --- the numbers elsewhere now follow the same rule ---------------------------------------------------------

def test_feedback_report_average_counts_approved_reviews_only(world):
    review(world, "a@example.com", "5", moderation_status="approved")
    review(world, "b@example.com", "1", moderation_status="rejected")
    review(world, "c@example.com", "1", moderation_status="pending")

    resp = client_for(world.db, world.owner).get(f"{API}/{world.event.id}/reports", params={"type": "feedback"})

    assert resp.status_code == 200
    data = resp.json()["data"] if "data" in resp.json() else resp.json()
    assert data["average_rating"] == 5.0
    assert data["total_reviews"] == 3  # the count still shows everything that was written


def test_feedback_report_average_is_null_with_no_approved_review(world):
    review(world, "c@example.com", "1", moderation_status="pending")

    resp = client_for(world.db, world.owner).get(f"{API}/{world.event.id}/reports", params={"type": "feedback"})

    data = resp.json()["data"] if "data" in resp.json() else resp.json()
    assert data["average_rating"] is None
