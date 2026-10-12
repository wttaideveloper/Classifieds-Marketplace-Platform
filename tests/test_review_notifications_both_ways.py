"""Review notifications in both directions, the moderator's name in the history, and the cross-role claims.

1. A new training / event review -> the owning Enterprise Admin(s) AND the Platform Super Admin(s), with `learner_name`.
2. A Super Admin moderates / deletes a training or event review -> the owning Enterprise Admin(s)
   (review_moderated / review_deleted); an Enterprise Admin or provider doing it -> the Super Admin(s), as before.
3. GET /reviews/audit returns `actor_name` (never an e-mail address).
4. Enterprise Admins (own items) and Super Admins (all items) can approve / reject / reset / delete; the public
   listing and the star numbers change at once; a deletion leaves its history row.
"""
import logging
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import review_admin
from app.core.catalog_access import get_optional_catalog_user
from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.models.event_aux_models import EventFeedback
from app.models.product_model import ProductReview
from app.models.review_audit_model import ReviewModerationLog
from app.models.service_model import ServiceReview
from app.models.training_model import TrainingEnrolment, TrainingReview
from app.services import chat_user_names, review_audit, review_notifications as rn
from tests.test_review_and_discussion_notifications import (  # noqa: F401  (fixtures and helpers are used by name)
    ENTERPRISE_ADMINS, ROOT, SUPER_ADMINS, delete, event_review, moderate, moderated, staff, training_review, world,
)
from tests.test_reviews_queue import A, B

# the world fixture silences the submitted-review delivery (it is another feature there); this file tests it
DELIVER_SUBMITTED = rn._deliver_submitted


@pytest.fixture
def submitted(world, monkeypatch):
    monkeypatch.setattr(rn, "_deliver_submitted", DELIVER_SUBMITTED)
    return world


def announce(world, module):
    review = {
        "training": lambda: training_review(world), "event": lambda: event_review(world),
        "product": lambda: world.db.query(ProductReview).filter_by(product_id=world.ids.a.product).one(),
        "service": lambda: world.db.query(ServiceReview).filter_by(service_id=world.ids.a.service).one(),
    }[module]()
    DELIVER_SUBMITTED(module, str(review.id), None)
    return review


def submitted_notices(world):
    return [n for n in world.sent if n["category"] == "review_submitted"]


# =============================================================================================================
# 1. a new review -> Enterprise Admin(s) and Super Admin(s), with learner_name
# =============================================================================================================

@pytest.mark.parametrize("module,learner", [("training", "Learner a"), ("event", "e-a")])
def test_a_new_review_goes_to_the_enterprise_admins_and_the_super_admins(submitted, module, learner):
    review = announce(submitted, module)

    owners, platform = submitted_notices(submitted)
    assert owners["user_ids"] == ENTERPRISE_ADMINS and owners["tenant_id"] == A
    assert platform["user_ids"] == SUPER_ADMINS and platform["tenant_id"] is None
    item_key = f"{module}_id"
    for notice in (owners, platform):
        meta = notice["metadata"]
        assert meta["category"] == "review_submitted" and meta["entity_type"] == module
        assert meta["review_id"] == str(review.id) and meta["status"] == "pending"
        assert meta[item_key] == meta["entity_id"] == meta["item_id"] == str(getattr(submitted.ids.a, module))
        assert meta["item_name"] == f"{module.title()} a"
        assert meta["learner_name"] == learner
        assert meta["rating"] == int(review.rating)
        assert notice["title"] == "New review" and notice["channels"] == ["in_app", "push"]


@pytest.mark.parametrize("module", ["training", "event"])
def test_no_email_address_appears_in_either_notification(submitted, module):
    announce(submitted, module)

    assert "@" not in str(submitted_notices(submitted))


@pytest.mark.parametrize("module", ["product", "service"])
def test_product_and_service_reviews_still_go_to_the_enterprise_admins_only(submitted, module):
    announce(submitted, module)

    [owners] = submitted_notices(submitted)
    assert owners["user_ids"] == ENTERPRISE_ADMINS


def test_a_product_reviewer_name_that_is_an_email_is_not_used(submitted):
    announce(submitted, "service")  # the seeded service review was written by "old@example.com"

    [owners] = submitted_notices(submitted)
    assert "learner_name" not in owners["metadata"] and "@" not in str(owners)


def test_a_product_reviewers_name_is_included(submitted):
    announce(submitted, "product")

    [owners] = submitted_notices(submitted)
    assert owners["metadata"]["learner_name"] == "Ravi"


def test_a_super_admin_who_also_owns_the_business_is_told_once(submitted):
    submitted.tenant_admins["ids"] = [SUPER_ADMINS[0], *ENTERPRISE_ADMINS]

    announce(submitted, "training")

    owners, platform = submitted_notices(submitted)
    assert SUPER_ADMINS[0] in owners["user_ids"] and platform["user_ids"] == [SUPER_ADMINS[1]]


def test_the_super_admins_are_told_even_when_no_enterprise_admin_is_found(submitted):
    submitted.tenant_admins["ids"] = []

    announce(submitted, "event")

    [platform] = submitted_notices(submitted)
    assert platform["user_ids"] == SUPER_ADMINS


def test_the_enterprise_admins_are_told_even_when_no_super_admin_is_found(submitted):
    submitted.platform_admins["ids"] = []

    announce(submitted, "training")

    [owners] = submitted_notices(submitted)
    assert owners["user_ids"] == ENTERPRISE_ADMINS


def test_announcing_the_same_review_twice_sends_each_copy_once(submitted):
    review = announce(submitted, "training")
    DELIVER_SUBMITTED("training", str(review.id), None)

    assert len(submitted_notices(submitted)) == 2


def test_the_learner_name_falls_back_to_one_identity_lookup(submitted):
    reviewer = uuid4()
    submitted.names[str(reviewer)] = "Looked Up"
    review = EventFeedback(
        event_id=submitted.ids.a.event, participant_email="no-registration@example.com", user_id=reviewer, rating="5",
        comment="", is_review=True, moderation_status="pending",
    )
    submitted.db.add(review)
    submitted.db.commit()

    DELIVER_SUBMITTED("event", str(review.id), None)

    [owners, _platform] = submitted_notices(submitted)
    assert owners["metadata"]["learner_name"] == "Looked Up"


def test_a_name_that_is_an_email_on_the_enrolment_is_left_out(submitted):
    submitted.db.query(TrainingEnrolment).filter_by(participant_email="l-a@example.com").update({"participant_name": "l-a@example.com"})
    submitted.db.commit()

    announce(submitted, "training")

    for notice in submitted_notices(submitted):
        assert "learner_name" not in notice["metadata"]


# =============================================================================================================
# 2. a Super Admin moderates / deletes -> the owning Enterprise Admin(s)
# =============================================================================================================

@pytest.mark.parametrize("module", ["training", "event"])
@pytest.mark.parametrize("action,verb", [("approved", "approved"), ("rejected", "rejected")])
def test_a_super_admins_decision_tells_the_enterprise_admins(world, module, action, verb):
    before = training_review(world) if module == "training" else event_review(world)
    previous, review_id = before.moderation_status, str(before.id)

    assert moderate(world, module, action, user=ROOT).status_code == 200

    [notice] = moderated(world)
    assert notice["user_ids"] == ENTERPRISE_ADMINS and notice["tenant_id"] == A
    assert notice["title"] == "Review moderated" and notice["channels"] == ["in_app"]
    meta = notice["metadata"]
    item_id = str(getattr(world.ids.a, module))
    assert meta["category"] == "review_moderated" and meta["entity_type"] == module
    assert meta["item_id"] == meta["entity_id"] == meta[f"{module}_id"] == item_id
    assert meta["item_name"] == f"{module.title()} a" and meta["review_id"] == review_id
    assert meta["action"] == action and meta["new_status"] == action and meta["previous_status"] == previous
    assert meta["actor_name"] == "Root" and meta["actor_role"] == "super_admin"
    assert f'Super Admin Root {verb} a review of "{module.title()} a"' in notice["message"]


@pytest.mark.parametrize("module", ["training", "event"])
def test_a_super_admin_moving_a_review_to_pending_is_announced_to_the_owners(world, module):
    moderate(world, module, "approved", user=ROOT)
    world.sent.clear()

    moderate(world, module, "pending", user=ROOT)

    [notice] = moderated(world)
    assert notice["metadata"]["previous_status"] == "approved" and "moved to pending" in notice["message"]


@pytest.mark.parametrize("module", ["training", "event"])
def test_a_super_admin_deleting_tells_the_enterprise_admins(world, module):
    review = training_review(world) if module == "training" else event_review(world)
    previous, review_id = review.moderation_status, str(review.id)

    assert delete(world, module, user=ROOT).status_code == 200

    [notice] = moderated(world, "review_deleted")
    assert notice["user_ids"] == ENTERPRISE_ADMINS and notice["title"] == "Review deleted"
    meta = notice["metadata"]
    assert meta["category"] == "review_deleted" and meta["item_id"] == str(getattr(world.ids.a, module))
    assert meta["review_id"] == review_id and meta["previous_status"] == previous
    assert meta["new_status"] == "deleted" and meta["action"] == "deleted"
    assert meta["actor_name"] == "Root" and meta["actor_role"] == "super_admin"
    assert "Super Admin Root deleted a review of" in notice["message"]


@pytest.mark.parametrize("module", ["training", "event"])
def test_a_super_admin_acting_never_notifies_the_super_admins(world, module):
    moderate(world, module, "approved", user=ROOT)
    delete(world, module)  # still ROOT: the previous call set the user

    assert all(set(n["user_ids"]) == set(ENTERPRISE_ADMINS) for n in world.sent)


def test_a_super_admin_who_also_owns_the_business_does_not_notify_themselves(world):
    world.tenant_admins["ids"] = [UUID(ROOT["id"]), *ENTERPRISE_ADMINS]

    moderate(world, "training", "approved", user=ROOT)

    [notice] = moderated(world)
    assert notice["user_ids"] == ENTERPRISE_ADMINS


def test_when_no_owner_can_be_found_the_super_admins_action_still_works(world):
    world.tenant_admins["ids"] = []

    assert moderate(world, "training", "approved", user=ROOT).status_code == 200

    assert moderated(world) == [] and training_review(world).moderation_status == "approved"


def test_an_enterprise_admin_acting_still_tells_the_super_admins(world):
    moderate(world, "training", "approved", user=staff())

    [notice] = moderated(world)
    assert notice["user_ids"] == SUPER_ADMINS and notice["metadata"]["actor_role"] == "admin"


# =============================================================================================================
# 3. GET /reviews/audit returns actor_name
# =============================================================================================================

@pytest.fixture
def audit_client(world):
    app = FastAPI()
    app.include_router(review_admin.router, prefix="/api/v1/reviews")
    app.dependency_overrides[get_db] = lambda: world.db

    def as_user(user):
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_optional_catalog_user] = lambda: user
        return TestClient(app, raise_server_exceptions=False)

    return as_user


def history(audit_client, user=ROOT, **params):
    resp = audit_client(user).get("/api/v1/reviews/audit", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


def test_the_history_shows_who_did_it_by_name(world, audit_client):
    moderate(world, "training", "approved", user=staff(name="Ada Admin"))

    [row] = history(audit_client)
    assert row["actor_name"] == "Ada Admin" and row["actor_role"] == "admin" and row["to_status"] == "approved"


def test_a_super_admins_action_is_named_too(world, audit_client):
    moderate(world, "event", "rejected", user=ROOT)

    [row] = history(audit_client)
    assert row["actor_name"] == "Root" and row["actor_role"] == "super_admin"


def test_an_email_is_never_stored_or_returned_as_the_name(world, audit_client):
    moderate(world, "training", "approved", user=staff(name="ada@example.com"))

    [row] = history(audit_client)
    assert row["actor_name"] is None
    assert world.db.query(ReviewModerationLog).one().actor_name is None


def test_an_older_row_without_a_saved_name_gets_the_current_name_from_the_identity_service(world, audit_client):
    actor = uuid4()
    world.names[str(actor)] = "Old Moderator"
    world.db.add(ReviewModerationLog(
        module="training", review_id=uuid4(), item_id=world.ids.a.training, item_name="Training a", tenant_id=A,
        from_status="pending", to_status="approved", actor_user_id=actor, actor_role="admin", actor_name=None,
    ))
    world.db.commit()

    [row] = history(audit_client)
    assert row["actor_name"] == "Old Moderator"


def test_a_failing_name_lookup_never_fails_the_history(world, audit_client, monkeypatch):
    world.db.add(ReviewModerationLog(
        module="training", review_id=uuid4(), item_id=world.ids.a.training, tenant_id=A,
        from_status="pending", to_status="approved", actor_user_id=uuid4(), actor_role="admin",
    ))
    world.db.commit()
    monkeypatch.setattr(chat_user_names, "resolve_display_names", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))

    [row] = history(audit_client)
    assert row["actor_name"] is None and row["to_status"] == "approved"


def test_the_saved_name_wins_over_a_later_lookup(world, audit_client):
    moderate(world, "training", "approved", user=staff(name="Ada Admin"))
    actor_id = world.db.query(ReviewModerationLog).one().actor_user_id
    world.names[str(actor_id)] = "Renamed Person"

    [row] = history(audit_client)
    assert row["actor_name"] == "Ada Admin"  # who it was at the time


# =============================================================================================================
# 4. the claims: permissions, immediate numbers, the deletion record
# =============================================================================================================

def public_listing(world, module):
    prefix = "trainings" if module == "training" else "events"
    return world.client.get(f"/api/v1/{prefix}/{getattr(world.ids.a, module)}/reviews").json()


@pytest.mark.parametrize("module", ["training", "event"])
@pytest.mark.parametrize("action", ["approved", "rejected", "pending"])
def test_an_enterprise_admin_can_do_every_status_change_on_their_own_items(world, module, action):
    moderate(world, module, "rejected", user=staff())  # start from something other than the target
    assert moderate(world, module, action, user=staff()).status_code == 200


@pytest.mark.parametrize("module", ["training", "event"])
@pytest.mark.parametrize("action", ["approved", "rejected", "pending"])
def test_a_super_admin_can_do_every_status_change_on_any_item(world, module, action):
    assert moderate(world, module, action, user=ROOT).status_code == 200


@pytest.mark.parametrize("module", ["training", "event"])
def test_an_enterprise_admin_cannot_touch_another_business(world, module):
    other = staff("admin", B)

    assert moderate(world, module, "approved", user=other).status_code == 403
    assert delete(world, module, user=other).status_code == 403
    assert world.sent == []


@pytest.mark.parametrize("module", ["training", "event"])
@pytest.mark.parametrize("who", ["admin", "super_admin"])
def test_both_roles_can_delete(world, module, who):
    user = staff() if who == "admin" else ROOT
    assert delete(world, module, user=user).status_code == 200


@pytest.mark.parametrize("module", ["training", "event"])
def test_approving_a_review_changes_the_public_listing_and_the_stars_at_once(world, module):
    assert public_listing(world, module)["count"] == 0  # the seeded review is pending

    moderate(world, module, "approved")
    live = public_listing(world, module)
    review = training_review(world) if module == "training" else event_review(world)
    rating = int(review.rating)
    assert live["count"] == 1 and live["average_rating"] == float(rating)
    assert live["rating_distribution"][str(rating)] == 1 and len(live["reviews"]) == 1

    moderate(world, module, "rejected")
    gone = public_listing(world, module)
    assert gone["count"] == 0 and gone["average_rating"] is None and gone["reviews"] == []
    assert sum(gone["rating_distribution"].values()) == 0

    moderate(world, module, "approved")
    delete(world, module)
    deleted = public_listing(world, module)
    assert deleted["count"] == 0 and deleted["average_rating"] is None and deleted["reviews"] == []


@pytest.mark.parametrize("module", ["training", "event"])
def test_a_deleted_review_is_gone_but_its_history_row_stays(world, module, audit_client):
    review = training_review(world) if module == "training" else event_review(world)
    previous, review_id = review.moderation_status, str(review.id)

    assert delete(world, module, user=ROOT).status_code == 200

    table = TrainingReview if module == "training" else EventFeedback
    assert world.db.query(table).filter(table.id == UUID(review_id)).count() == 0
    [row] = [r for r in history(audit_client) if r["review_id"] == review_id]
    assert row["to_status"] == "deleted" and row["from_status"] == previous
    assert row["actor_role"] == "super_admin" and row["actor_name"] == "Root" and row["created_at"]
