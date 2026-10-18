"""A Super Admin must give a reason to reject or to delete someone else's training or event review.

The reason is 3 to 500 characters, is kept in the moderation history and comes back from GET /reviews/audit as `reason`.
Rejecting, holding back (pending) and deleting need a reason in every module; approving and an author deleting their own
review do not. The author sees the reason on their review (`reason`).
"""
from uuid import UUID

import pytest

from app.models.event_aux_models import EventFeedback
from app.models.training_model import TrainingReview
from tests.test_reviews_audit_and_delete import (  # noqa: F401  (the world fixture and helpers are used by name)
    AUTHORS, MODULE_PATHS, ROOT, history, item_of, review_id, staff, world,
)

REASONED = ["training", "event"]
ALL_MODULES = ["training", "product", "service", "event"]
TABLES = {"training": TrainingReview, "event": EventFeedback}


def reject(world, module, rid, via="queue", **body):
    world.as_user(ROOT)
    url = f"/api/v1/reviews/{module}/{rid}/moderate" if via == "queue" else f"/api/v1/{MODULE_PATHS[module]}/{item_of(world, module)}/reviews/{rid}/moderate"
    return world.client.patch(url, json={"action": "rejected", **body})


def delete(world, module, rid, via="queue", body=None, query=None, user=ROOT):
    world.as_user(user)
    url = f"/api/v1/reviews/{module}/{rid}" if via == "queue" else f"/api/v1/{MODULE_PATHS[module]}/{item_of(world, module)}/reviews/{rid}"
    if query:
        url += f"?reason={query}"
    return world.client.request("DELETE", url, json=body) if body is not None else world.client.delete(url)


def status_of(world, module, rid):
    world.db.expire_all()
    return world.db.get(TABLES[module], UUID(rid)).moderation_status


# --- rejecting ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("via", ["queue", "module"])
@pytest.mark.parametrize("module", REASONED)
def test_a_rejection_without_a_reason_is_refused_and_nothing_changes(world, module, via):
    rid = review_id(world, module)
    before = status_of(world, module, rid)

    for body in ({}, {"reason": None}, {"reason": ""}, {"reason": "   "}):
        resp = reject(world, module, rid, via, **body)
        assert resp.status_code == 422, body
        assert resp.json() == {"detail": "A reason is required to reject a review"}

    assert status_of(world, module, rid) == before and history(world, review_id=UUID(rid)) == []


@pytest.mark.parametrize("module", REASONED)
def test_a_reason_must_be_between_3_and_500_characters(world, module):
    rid = review_id(world, module)

    short = reject(world, module, rid, reason="no")
    long = reject(world, module, rid, reason="x" * 501)
    not_text = reject(world, module, rid, reason=123)

    assert short.status_code == 422 and short.json() == {"detail": "The reason must be at least 3 characters"}
    assert long.status_code == 422 and long.json() == {"detail": "The reason must be at most 500 characters"}
    assert not_text.status_code == 422 and isinstance(not_text.json()["detail"], list)     # a wrong JSON type: the usual body error
    assert reject(world, module, rid, reason="x" * 500).status_code == 200                  # exactly 500 is fine


@pytest.mark.parametrize("via", ["queue", "module"])
@pytest.mark.parametrize("module", REASONED)
def test_a_rejection_with_a_reason_is_saved_in_the_history_and_returned_by_the_audit_api(world, module, via):
    rid = review_id(world, module)

    resp = reject(world, module, rid, via, reason="  Contains   personal   details  ")

    assert resp.status_code == 200, resp.text
    [row] = history(world, review_id=UUID(rid))
    assert row.to_status == "rejected" and row.reason == "Contains personal details"        # trimmed, on one line
    world.as_user(ROOT)
    [item] = world.client.get("/api/v1/reviews/audit", params={"review_id": rid}).json()["items"]
    assert item["reason"] == "Contains personal details" and item["to_status"] == "rejected"
    world.as_user(staff())                                                                  # the owning Enterprise Admin sees it too
    [seen] = world.client.get("/api/v1/reviews/audit", params={"review_id": rid}).json()["items"]
    assert seen["reason"] == "Contains personal details"


@pytest.mark.parametrize("module", ALL_MODULES)
def test_approving_needs_no_reason_but_holding_back_does(world, module):
    rid = review_id(world, module)
    world.as_user(ROOT)
    url = f"/api/v1/reviews/{module}/{rid}/moderate"
    current = world.client.get("/api/v1/reviews/manage", params={"module": module}).json()["items"][0]["moderation_status"]
    if current == "rejected":
        assert world.client.patch(url, json={"action": "approved"}).status_code == 200      # rejected -> approved: no reason
        current = "approved"
    if current == "pending":
        assert world.client.patch(url, json={"action": "approved"}).status_code == 200
    assert world.client.patch(url, json={"action": "pending"}).status_code == 422
    assert world.client.patch(url, json={"action": "pending"}).json() == {"detail": "A reason is required to move a review to pending"}
    assert world.client.patch(url, json={"action": "pending", "reason": "Needs checking"}).status_code == 200
    assert [r.reason for r in history(world, review_id=UUID(rid))][-1] == "Needs checking"


@pytest.mark.parametrize("module", ["product", "service"])
def test_product_and_service_rejections_and_deletes_need_a_reason_too(world, module):
    rid = review_id(world, module)
    world.as_user(ROOT)
    url = f"/api/v1/reviews/{module}/{rid}/moderate"
    target = "pending" if module == "service" else "rejected"       # the seeded service review is already rejected
    if module == "service":
        assert world.client.patch(url, json={"action": "approved"}).status_code == 200

    refused = world.client.patch(url, json={"action": target})
    ok = world.client.patch(url, json={"action": target, "reason": "Off topic"})
    refused_delete = world.client.delete(f"/api/v1/reviews/{module}/{rid}")
    deleted = world.client.request("DELETE", f"/api/v1/reviews/{module}/{rid}", json={"reason": "Abusive language"})

    assert refused.status_code == 422 and ok.status_code == 200
    assert refused_delete.status_code == 422 and deleted.status_code == 200
    assert [r.reason for r in history(world, review_id=UUID(rid)) if r.reason] == ["Off topic", "Abusive language"]


def test_a_reason_is_still_checked_after_the_permission(world):
    rid = review_id(world, "training")
    world.as_user(staff())      # an Enterprise Admin: refused for being one, whatever the reason says

    resp = world.client.patch(f"/api/v1/reviews/training/{rid}/moderate", json={"action": "rejected"})

    assert resp.status_code == 403 and resp.json()["detail"] == "Only a Super Admin can moderate reviews"


# --- deleting -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("via", ["queue", "module"])
@pytest.mark.parametrize("module", REASONED)
def test_a_super_admin_delete_without_a_reason_is_refused_and_nothing_changes(world, module, via):
    rid = review_id(world, module)
    before = status_of(world, module, rid)

    for resp in (
        delete(world, module, rid, via), delete(world, module, rid, via, body={}), delete(world, module, rid, via, body={"reason": "  "}),
        delete(world, module, rid, via, body={"reason": "ab"}), delete(world, module, rid, via, body={"reason": "x" * 501}),
    ):
        assert resp.status_code == 422, resp.text
    assert delete(world, module, rid, via).json() == {"detail": "A reason is required to delete a review"}
    assert delete(world, module, rid, via, body={"reason": "ab"}).json() == {"detail": "The reason must be at least 3 characters"}
    assert delete(world, module, rid, via, body={"reason": "x" * 501}).json() == {"detail": "The reason must be at most 500 characters"}
    assert status_of(world, module, rid) == before and history(world, review_id=UUID(rid)) == []


@pytest.mark.parametrize("via", ["queue", "module"])
@pytest.mark.parametrize("module", REASONED)
def test_a_deletion_with_a_reason_is_saved_and_returned_by_the_audit_api(world, module, via):
    rid = review_id(world, module)

    resp = delete(world, module, rid, via, body={"reason": "Abusive language"})

    assert resp.status_code == 200 and resp.json() == {"message": "Review deleted"}
    assert status_of(world, module, rid) == "deleted"
    [row] = history(world, review_id=UUID(rid))
    assert row.to_status == "deleted" and row.reason == "Abusive language"
    world.as_user(ROOT)
    [item] = world.client.get("/api/v1/reviews/audit", params={"review_id": rid}).json()["items"]
    assert item["reason"] == "Abusive language" and item["to_status"] == "deleted"


@pytest.mark.parametrize("module", REASONED)
def test_the_reason_can_also_come_as_a_query_parameter(world, module):
    rid = review_id(world, module)

    resp = delete(world, module, rid, query="Spam")

    assert resp.status_code == 200 and history(world, review_id=UUID(rid))[0].reason == "Spam"


@pytest.mark.parametrize("module", REASONED)
def test_the_author_deleting_their_own_review_needs_no_reason(world, module):
    rid = review_id(world, module)

    resp = delete(world, module, rid, via="module", user=AUTHORS[module])

    assert resp.status_code == 200 and history(world, review_id=UUID(rid)) == []


@pytest.mark.parametrize("module", REASONED)
def test_staff_are_refused_before_the_reason_matters(world, module):
    rid = review_id(world, module)

    resp = delete(world, module, rid, user=staff(), body={"reason": "Not allowed"})

    assert resp.status_code == 403 and resp.json()["detail"] == "You can only delete your own review"
