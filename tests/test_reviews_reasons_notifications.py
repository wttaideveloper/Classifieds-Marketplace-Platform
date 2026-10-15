"""The reason for rejecting or deleting a training or event review, in the notifications.

metadata.reason is on: review_rejected (to the author), review_moderated with action rejected (to the owning Enterprise
Admins), review_deleted (to the owning Enterprise Admins and, new, to the author). It is in the feed and the realtime
event only: the text of the notification never contains it and the push payload leaves it out.
Approving or holding back a review, and every product / service notification, stay exactly as they were.
"""
from uuid import uuid4

import pytest

from app.services import notification_delivery_service, review_notifications as rn
from tests.test_review_and_discussion_notifications import (  # noqa: F401  (the world fixture and helpers are used by name)
    ENTERPRISE_ADMINS, ROOT, delete, event_review, moderate, moderated, review_of, training_review, world,
)

DELIVER_DECISION = rn._deliver_decision
AUTHOR = uuid4()
REASON = "Contains personal details"


@pytest.fixture
def notes(world, monkeypatch):
    monkeypatch.setattr(rn, "_deliver_decision", DELIVER_DECISION)            # the author's notice is under test here
    monkeypatch.setattr(rn, "_author_user_id", lambda *a, **k: AUTHOR)
    return world


def body(world, module, action, reason=None):
    """moderate() with this reason."""
    review = training_review(world) if module == "training" else event_review(world)
    path = "trainings" if module == "training" else "events"
    item = world.ids.a.training if module == "training" else world.ids.a.event
    payload = {"action": action, **({"reason": reason} if reason is not None else {})}
    return world.client.patch(f"/api/v1/{path}/{item}/reviews/{review.id}/moderate", json=payload)


def removal(world, module, reason):
    review = training_review(world) if module == "training" else event_review(world)
    path = "trainings" if module == "training" else "events"
    item = world.ids.a.training if module == "training" else world.ids.a.event
    return world.client.request("DELETE", f"/api/v1/{path}/{item}/reviews/{review.id}", json={"reason": reason})


@pytest.mark.parametrize("module", ["training", "event"])
def test_a_rejection_sends_the_reason_to_the_author_and_the_owning_enterprise_admins(notes, module):
    assert body(notes, module, "rejected", REASON).status_code == 200

    [author_notice] = moderated(notes, "review_rejected")
    assert author_notice["user_ids"] == [AUTHOR] and author_notice["channels"] == ["in_app", "push"]
    assert author_notice["metadata"]["reason"] == REASON and author_notice["metadata"]["status"] == "rejected"
    assert author_notice["metadata"]["category"] == "review_rejected" and author_notice["metadata"]["entity_type"] == module
    [admin_notice] = moderated(notes, "review_moderated")
    assert admin_notice["user_ids"] == ENTERPRISE_ADMINS and admin_notice["channels"] == ["in_app"]
    assert admin_notice["metadata"]["reason"] == REASON and admin_notice["metadata"]["action"] == "rejected"


@pytest.mark.parametrize("module", ["training", "event"])
def test_the_reason_is_never_in_the_text_a_push_shows(notes, module):
    body(notes, module, "rejected", REASON)

    for notice in notes.sent:
        assert REASON not in notice["title"] and REASON not in notice["message"]
    [author_notice] = moderated(notes, "review_rejected")
    assert author_notice["message"].endswith("was not approved.") and author_notice["title"] == "Review not approved"


def test_the_push_payload_leaves_the_reason_out():
    assert "reason" in notification_delivery_service.PUSH_OMITTED_METADATA_KEYS


@pytest.mark.parametrize("module", ["training", "event"])
def test_approving_and_holding_back_carry_no_reason(notes, module):
    body(notes, module, "approved")
    body(notes, module, "pending", "ignored")

    assert moderated(notes, "review_approved")[0]["metadata"].get("reason") is None
    for notice in moderated(notes, "review_moderated"):
        assert "reason" not in notice["metadata"]


@pytest.mark.parametrize("module", ["training", "event"])
def test_a_deletion_sends_the_reason_to_the_enterprise_admins_and_now_the_author(notes, module):
    assert removal(notes, module, REASON).status_code == 200

    notices = moderated(notes, "review_deleted")
    assert len(notices) == 2
    admins = next(n for n in notices if n["user_ids"] == ENTERPRISE_ADMINS)
    author = next(n for n in notices if n["user_ids"] == [AUTHOR])
    assert admins["metadata"]["reason"] == REASON and admins["metadata"]["new_status"] == "deleted"
    assert admins["channels"] == ["in_app"] and admins["metadata"]["actor_role"] == "super_admin"
    assert author["metadata"]["reason"] == REASON and author["metadata"]["recipient"] == "author"
    assert author["metadata"]["status"] == "deleted" and author["metadata"]["entity_type"] == module
    assert author["channels"] == ["in_app", "push"] and author["title"] == "Review deleted"
    assert author["message"].endswith("was deleted by an administrator.") and REASON not in author["message"]


@pytest.mark.parametrize("module", ["training", "event"])
def test_the_author_deleting_their_own_review_notifies_nobody(notes, module):
    review = training_review(notes) if module == "training" else event_review(notes)
    author = {"id": str(uuid4()), "role": "customer", "email": review.participant_email, "name": "The Author"}

    assert delete(notes, module, user=author).status_code == 200

    assert notes.sent == []


@pytest.mark.parametrize("module", ["training", "event"])
def test_a_refused_request_notifies_nobody(notes, module):
    assert body(notes, module, "rejected").status_code == 422                 # no reason
    assert removal(notes, module, "").status_code == 422

    assert notes.sent == []


def test_the_author_notice_is_skipped_when_the_author_cannot_be_found(notes, monkeypatch):
    monkeypatch.setattr(rn, "_author_user_id", lambda *a, **k: None)

    assert removal(notes, "training", REASON).status_code == 200

    [only] = moderated(notes, "review_deleted")
    assert only["user_ids"] == ENTERPRISE_ADMINS


def test_product_notifications_are_unchanged(notes):
    review = review_of(notes, "product")
    notes.as_user(ROOT)

    notes.client.patch(f"/api/v1/products/{notes.ids.a.product}/reviews/{review.id}/moderate", json={"action": "rejected"})
    notes.client.delete(f"/api/v1/products/{notes.ids.a.product}/reviews/{review.id}")

    for notice in notes.sent:
        assert "reason" not in notice["metadata"]
    assert [n["user_ids"] for n in moderated(notes, "review_deleted")] == [ENTERPRISE_ADMINS]          # no author notice
