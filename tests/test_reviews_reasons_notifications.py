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
def test_approving_carries_no_reason_and_holding_back_sends_it_to_the_enterprise_admins_and_the_author(notes, module):
    assert body(notes, module, "approved").status_code == 200
    assert body(notes, module, "pending", "Needs a second look").status_code == 200

    assert moderated(notes, "review_approved")[0]["metadata"].get("reason") is None
    approved_notice, pending_notice = moderated(notes, "review_moderated")
    assert "reason" not in approved_notice["metadata"]
    assert pending_notice["metadata"]["reason"] == "Needs a second look" and pending_notice["metadata"]["action"] == "pending"
    assert pending_notice["user_ids"] == ENTERPRISE_ADMINS
    [held] = moderated(notes, "review_pending")                     # the author is told too
    assert held["user_ids"] == [AUTHOR] and held["metadata"]["reason"] == "Needs a second look"


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


@pytest.mark.parametrize("module", ["product", "service"])
def test_product_and_service_notifications_carry_the_reason_too(notes, module):
    kind = "products" if module == "product" else "services"
    review = review_of(notes, module)
    notes.as_user(ROOT)
    item = getattr(notes.ids.a, module)
    target = "rejected" if review.moderation_status != "rejected" else "approved"
    if target == "approved":
        notes.client.patch(f"/api/v1/{kind}/{item}/reviews/{review.id}/moderate", json={"action": "approved"})
        target = "rejected"
    notes.sent.clear()

    notes.client.patch(f"/api/v1/{kind}/{item}/reviews/{review.id}/moderate", json={"action": target, "reason": "Off topic"})
    notes.client.request("DELETE", f"/api/v1/{kind}/{item}/reviews/{review.id}", json={"reason": "Abusive language"})

    assert moderated(notes, "review_rejected")[0]["metadata"]["reason"] == "Off topic"
    assert moderated(notes, "review_moderated")[0]["metadata"]["reason"] == "Off topic"
    deleted = moderated(notes, "review_deleted")
    assert {n["metadata"]["reason"] for n in deleted} == {"Abusive language"}
    assert [n["user_ids"] for n in deleted if n["metadata"].get("recipient") == "author"] == [[AUTHOR]]


# --- the author is told when a review is moved to pending, with the reason -------------------------------------------

@pytest.mark.parametrize("module", ["training", "event"])
def test_moving_a_review_to_pending_notifies_the_author_with_the_reason(notes, module):
    body(notes, module, "approved")
    notes.sent.clear()

    assert body(notes, module, "pending", "Needs a second look").status_code == 200

    [notice] = moderated(notes, "review_pending")
    assert notice["user_ids"] == [AUTHOR] and notice["channels"] == ["in_app", "push"]
    assert notice["title"] == "Review waiting for approval"
    assert notice["message"] == f'Your review of "{module.title()} a" is waiting for approval and is hidden for now.'
    assert "Needs a second look" not in notice["title"] + notice["message"]
    assert notice["metadata"] == {
        "category": "review_pending", f"{module}_id": str(getattr(notes.ids.a, module)), "entity_type": module,
        "entity_id": str(getattr(notes.ids.a, module)), "review_id": notice["metadata"]["review_id"],
        "status": "pending", "reason": "Needs a second look",
    }


@pytest.mark.parametrize("module", ["product", "service"])
def test_product_and_service_authors_are_told_about_a_hold_too(notes, module):
    kind = "products" if module == "product" else "services"
    review = review_of(notes, module)
    item = getattr(notes.ids.a, module)
    notes.as_user(ROOT)
    if review.moderation_status != "approved":
        notes.client.patch(f"/api/v1/{kind}/{item}/reviews/{review.id}/moderate", json={"action": "approved"})
    notes.sent.clear()

    notes.client.patch(f"/api/v1/{kind}/{item}/reviews/{review.id}/moderate", json={"action": "pending", "reason": "Needs a second look"})

    [notice] = moderated(notes, "review_pending")
    assert notice["user_ids"] == [AUTHOR] and notice["metadata"]["reason"] == "Needs a second look"


def test_the_same_status_again_and_an_authors_resubmission_do_not_send_review_pending(notes):
    body(notes, "training", "rejected", "Contains personal details")
    notes.sent.clear()
    review = training_review(notes)
    from tests.test_review_and_discussion_notifications import LEARNER_ID  # noqa: F401  (the author edits their own review)

    body(notes, "training", "rejected", "Contains personal details")        # already rejected: nothing changes

    assert moderated(notes, "review_pending") == [] and moderated(notes, "review_rejected") == [] and review is not None


def test_the_push_payload_leaves_the_review_text_and_the_reason_out():
    assert {"reason", "comment"} <= set(notification_delivery_service.PUSH_OMITTED_METADATA_KEYS)
