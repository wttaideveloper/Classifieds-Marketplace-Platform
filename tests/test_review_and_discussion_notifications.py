"""Two notifications on the existing feed (GET /api/v1/users/me/notifications) and the generic Socket.IO event.

1. A learner posts a question in a training discussion -> the owning Enterprise Admin(s):
   training_question_posted {learner_name, training_name, training_id, discussion_id}.
2. An Enterprise Admin / provider moderates or deletes a training or event review -> the Platform Super Admin(s):
   review_moderated (approve / reject / pending) and review_deleted, with entity_type, entity_id, review_id,
   previous_status, new_status, action and the acting admin's name.

Real routes and real SQLite data; the background thread, the identity lookups and the delivery are stubbed.
"""
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import event, product, training
from app.core.catalog_access import get_optional_catalog_user
from app.core.dependencies import get_current_user
from app.db.database import Base, get_db
from app.models.event_aux_models import EventFeedback
from app.models.notification_model import NotificationEventLog
from app.models.product_model import ProductReview
from app.models.review_audit_model import ReviewModerationLog
from app.models.training_model import Training, TrainingReview
from app.services import (
    chat_user_names, event_notification_service, notification_service, review_notifications as rn,
    training_notifications, training_workflow_notifications as twn,
)
from tests.event_sql_support import make_session
from tests.test_reviews_queue import A, B, seed

SUPER_ADMINS = [uuid4(), uuid4()]
ENTERPRISE_ADMINS = [uuid4(), uuid4()]
LEARNER_ID = uuid4()


def staff(role="admin", tenant=A, name="Ada Admin"):
    return {"id": str(uuid4()), "email": f"{role}@example.com", "name": name, "role": role, "tenant_id": str(tenant)}


ROOT = {"id": str(uuid4()), "role": "super_admin", "email": "root@example.com", "name": "Root"}


@pytest.fixture
def world(monkeypatch):
    db = make_session()
    Base.metadata.create_all(db.bind, tables=[NotificationEventLog.__table__, ReviewModerationLog.__table__])
    ids = seed(db)

    @contextmanager
    def session_factory():  # the notification code opens its own session; hand it this one without closing it
        yield db

    monkeypatch.setattr("app.db.database.SessionLocal", session_factory)
    monkeypatch.setattr(rn, "_dispatch", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(training_notifications, "_dispatch", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(rn, "_deliver_decision", lambda *a, **k: None)      # the author's notice is another feature
    monkeypatch.setattr(rn, "_deliver_submitted", lambda *a, **k: None)

    sent, platform_admins, tenant_admins, names = [], {"ids": SUPER_ADMINS}, {"ids": ENTERPRISE_ADMINS}, {}
    monkeypatch.setattr(
        notification_service, "create_automatic_notification",
        lambda _db, **kw: sent.append(kw) or SimpleNamespace(id=uuid4()),
    )
    monkeypatch.setattr(event_notification_service, "resolve_platform_admin_user_ids", lambda token=None: platform_admins["ids"])
    monkeypatch.setattr(
        event_notification_service, "resolve_enterprise_admin_user_ids",
        lambda _db, item, access_token=None: (tenant_admins["ids"], A),
    )
    monkeypatch.setattr(
        chat_user_names, "resolve_display_names",
        lambda user_ids, **kw: {str(u): names.get(str(u)) for u in user_ids},
    )

    app = FastAPI()
    app.include_router(training.router, prefix="/api/v1/trainings")
    app.include_router(product.router, prefix="/api/v1/products")
    app.include_router(event.router, prefix="/api/v1/events")
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app, raise_server_exceptions=False)

    def as_user(user):
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_optional_catalog_user] = lambda: user
        return client

    as_user(staff())
    yield SimpleNamespace(
        client=client, as_user=as_user, db=db, ids=ids, sent=sent, platform_admins=platform_admins,
        tenant_admins=tenant_admins, names=names,
    )
    db.close()


def moderated(world, category="review_moderated"):
    return [n for n in world.sent if n["category"] == category]


def training_review(world, tag="a"):
    return world.db.query(TrainingReview).filter_by(training_id=getattr(world.ids, tag).training).one()


def event_review(world, tag="a"):
    return world.db.query(EventFeedback).filter_by(event_id=getattr(world.ids, tag).event, is_review=True).one()


def moderate(world, module, action, user=None):
    if user is not None:
        world.as_user(user)
    if module == "training":
        r = training_review(world)
        return world.client.patch(f"/api/v1/trainings/{world.ids.a.training}/reviews/{r.id}/moderate", json={"action": action})
    r = event_review(world)
    return world.client.patch(f"/api/v1/events/{world.ids.a.event}/reviews/{r.id}/moderate", json={"action": action})


def delete(world, module, user=None):
    if user is not None:
        world.as_user(user)
    if module == "training":
        r = training_review(world)
        return world.client.delete(f"/api/v1/trainings/{world.ids.a.training}/reviews/{r.id}")
    r = event_review(world)
    return world.client.delete(f"/api/v1/events/{world.ids.a.event}/reviews/{r.id}")


# =============================================================================================================
# 2. review moderation / deletion -> Super Admin
# =============================================================================================================

@pytest.mark.parametrize("module", ["training", "event"])
@pytest.mark.parametrize("action,verb", [("approved", "approved"), ("rejected", "rejected")])
def test_a_status_change_goes_to_the_super_admins_with_the_full_metadata(world, module, action, verb):
    review_before = training_review(world) if module == "training" else event_review(world)
    previous, review_id = review_before.moderation_status, str(review_before.id)

    assert moderate(world, module, action).status_code == 200

    [notice] = moderated(world)
    assert notice["user_ids"] == SUPER_ADMINS and notice["channels"] == ["in_app"]
    assert notice["title"] == "Review moderated"
    item_id = str(getattr(world.ids.a, module))
    item_key = "training_id" if module == "training" else "event_id"
    meta = notice["metadata"]
    assert meta["category"] == "review_moderated"
    assert meta["entity_type"] == module and meta["entity_id"] == item_id and meta[item_key] == item_id
    assert meta["review_id"] == review_id
    assert meta["previous_status"] == previous and meta["new_status"] == action and meta["action"] == action
    assert meta["actor_name"] == "Ada Admin" and meta["actor_role"] == "admin"
    assert meta["item_name"] == f"{module.title()} a"
    assert f'Enterprise Admin Ada Admin {verb} a review of "{module.title()} a"' in notice["message"]


@pytest.mark.parametrize("module", ["training", "event"])
def test_moving_a_review_back_to_pending_is_announced(world, module):
    moderate(world, module, "approved")
    world.sent.clear()

    assert moderate(world, module, "pending").status_code == 200

    [notice] = moderated(world)
    assert notice["metadata"]["previous_status"] == "approved" and notice["metadata"]["new_status"] == "pending"
    assert "moved to pending" in notice["message"]


@pytest.mark.parametrize("module", ["training", "event"])
def test_each_change_is_its_own_notification(world, module):
    moderate(world, module, "approved")
    moderate(world, module, "rejected")
    moderate(world, module, "pending")

    assert [(n["metadata"]["previous_status"], n["metadata"]["new_status"]) for n in moderated(world)] == [
        ("pending", "approved"), ("approved", "rejected"), ("rejected", "pending"),
    ]


@pytest.mark.parametrize("module", ["training", "event"])
def test_repeating_the_same_status_announces_nothing(world, module):
    moderate(world, module, "approved")
    world.sent.clear()

    moderate(world, module, "approved")

    assert moderated(world) == []


@pytest.mark.parametrize("module", ["training", "event"])
def test_a_super_admin_moderating_tells_the_enterprise_admins_not_the_super_admins(world, module):
    assert moderate(world, module, "approved", user=ROOT).status_code == 200

    [notice] = moderated(world)
    assert notice["user_ids"] == ENTERPRISE_ADMINS  # never the Super Admins (see test_review_notifications_both_ways.py)


@pytest.mark.parametrize("module", ["training", "event"])
@pytest.mark.parametrize("role,label", [("admin", "Enterprise Admin"), ("provider", "Provider")])
def test_deleting_a_review_goes_to_the_super_admins(world, module, role, label):
    review = training_review(world) if module == "training" else event_review(world)
    previous, review_id = review.moderation_status, str(review.id)

    assert delete(world, module, user=staff(role)).status_code == 200

    [notice] = moderated(world, "review_deleted")
    assert notice["user_ids"] == SUPER_ADMINS and notice["title"] == "Review deleted"
    meta = notice["metadata"]
    assert meta["category"] == "review_deleted" and meta["entity_type"] == module
    assert meta["review_id"] == review_id
    assert meta["previous_status"] == previous and meta["new_status"] == "deleted" and meta["action"] == "deleted"
    assert meta["actor_name"] == "Ada Admin" and meta["actor_role"] == role
    assert f"{label} Ada Admin deleted a review of" in notice["message"]
    assert moderated(world) == []  # a delete is not also announced as a status change


@pytest.mark.parametrize("module", ["training", "event"])
def test_the_author_deleting_their_own_review_is_not_moderation(world, module):
    review = training_review(world) if module == "training" else event_review(world)
    author = {"id": str(uuid4()), "role": "customer", "email": review.participant_email, "name": "The Author"}

    assert delete(world, module, user=author).status_code == 200

    assert world.sent == []


@pytest.mark.parametrize("module", ["training", "event"])
def test_a_super_admin_deleting_tells_the_enterprise_admins_not_the_super_admins(world, module):
    assert delete(world, module, user=ROOT).status_code == 200

    [notice] = moderated(world, "review_deleted")
    assert notice["user_ids"] == ENTERPRISE_ADMINS


@pytest.mark.parametrize("module", ["training", "event"])
def test_a_refused_delete_announces_nothing(world, module):
    other_business = staff("admin", B)

    assert delete(world, module, user=other_business).status_code == 403

    assert world.sent == []


def test_product_reviews_are_not_part_of_this_notification(world):
    review = world.db.query(ProductReview).filter_by(product_id=world.ids.a.product).one()

    resp = world.client.patch(f"/api/v1/products/{world.ids.a.product}/reviews/{review.id}/moderate", json={"action": "approved"})

    assert resp.status_code == 200 and moderated(world) == []


def test_a_token_name_that_is_an_email_is_looked_up_instead(world):
    admin = staff(name="ada@example.com")
    world.names[admin["id"]] = "Ada Lovelace"

    moderate(world, "training", "approved", user=admin)

    [notice] = moderated(world)
    assert notice["metadata"]["actor_name"] == "Ada Lovelace" and "Ada Lovelace" in notice["message"]


def test_no_name_anywhere_still_notifies_without_inventing_one(world):
    admin = staff(name=None)

    moderate(world, "event", "rejected", user=admin)

    [notice] = moderated(world)
    assert "actor_name" not in notice["metadata"]
    assert notice["message"].startswith("An Enterprise Admin rejected")
    assert "@" not in notice["message"]


def test_no_super_admin_found_sends_nothing_and_does_not_break_the_moderation(world):
    world.platform_admins["ids"] = []

    resp = moderate(world, "training", "approved")

    assert resp.status_code == 200 and world.sent == []
    assert training_review(world).moderation_status == "approved"


@pytest.mark.parametrize("module", ["training", "event"])
def test_a_failing_notification_never_breaks_the_moderation_or_the_delete(world, module, monkeypatch):
    monkeypatch.setattr(rn, "_dispatch_platform_notice", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("queue down")))

    assert moderate(world, module, "approved").status_code == 200
    assert delete(world, module).status_code == 200


def test_the_same_change_is_delivered_once_even_if_dispatched_twice(world):
    kwargs = dict(
        review_id=uuid4(), item_id=uuid4(), item_name="Course", tenant_id=A, previous_status="pending",
        new_status="approved", actor={"id": str(uuid4()), "name": "Ada", "role": "admin"}, access_token=None,
        stamp="2026-10-12T10:00:00",
    )
    rn._deliver_platform_notice("review_moderated", "training", **kwargs)
    rn._deliver_platform_notice("review_moderated", "training", **kwargs)

    assert len(moderated(world)) == 1


# =============================================================================================================
# 1. a learner's question in a training discussion -> the Enterprise Admin(s)
# =============================================================================================================

def learner(name="Lena", email="l-a@example.com"):
    return {"id": str(LEARNER_ID), "role": "customer", "email": email, "name": name}


def ask(world, text="How do I reset my password?", user=None):
    world.as_user(user or learner())
    return world.client.post(f"/api/v1/trainings/{world.ids.a.training}/discussions", json={"question": text})


def test_a_learners_question_notifies_the_enterprise_admins(world):
    resp = ask(world)

    assert resp.status_code == 201, resp.text
    [notice] = moderated(world, "training_question_posted")
    assert notice["user_ids"] == ENTERPRISE_ADMINS and notice["tenant_id"] == A
    assert notice["title"] == "New question in a training discussion"
    meta = notice["metadata"]
    assert meta["category"] == "training_question_posted"
    assert meta["learner_name"] == "Lena" and meta["training_name"] == "Training a"
    assert meta["training_id"] == str(world.ids.a.training) == meta["entity_id"] and meta["entity_type"] == "training"
    assert meta["discussion_id"] == resp.json()["id"]
    assert 'Lena asked in "Training a": How do I reset my password?' == notice["message"]
    assert notice["channels"] == ["in_app", "push"]


def test_the_question_is_saved_before_anyone_is_told(world):
    ask(world)

    stored = world.db.get(Training, world.ids.a.training).discussions
    assert len(stored) == 1 and stored[0]["question"] == "How do I reset my password?"


def test_a_token_name_that_is_an_email_falls_back_to_the_enrolment_name(world):
    ask(world, user=learner(name="l-a@example.com"))

    [notice] = moderated(world, "training_question_posted")
    assert notice["metadata"]["learner_name"] == "Learner a"  # the name on their enrolment


def test_without_any_name_the_identity_service_is_asked(world):
    stranger = learner(name=None, email="unenrolled@example.com")
    world.names[stranger["id"]] = "Looked Up"

    ask(world, user=stranger)

    [notice] = moderated(world, "training_question_posted")
    assert notice["metadata"]["learner_name"] == "Looked Up"


def test_with_no_name_at_all_it_says_a_learner_and_never_an_email(world):
    ask(world, user=learner(name=None, email="unenrolled@example.com"))

    [notice] = moderated(world, "training_question_posted")
    assert "learner_name" not in notice["metadata"]
    assert notice["message"].startswith("A learner asked") and "@" not in notice["message"]


@pytest.mark.parametrize("role", ["admin", "provider", "super_admin"])
def test_a_question_from_staff_is_not_announced(world, role):
    resp = ask(world, user=staff(role))

    assert resp.status_code == 201 and moderated(world, "training_question_posted") == []


def test_the_asker_is_never_told_about_their_own_question(world):
    world.tenant_admins["ids"] = [LEARNER_ID, *ENTERPRISE_ADMINS]  # an admin who is also enrolled as a learner

    ask(world)

    [notice] = moderated(world, "training_question_posted")
    assert notice["user_ids"] == ENTERPRISE_ADMINS


def test_a_long_question_is_shortened_in_the_message_only(world):
    long_text = "word " * 100

    ask(world, long_text)

    [notice] = moderated(world, "training_question_posted")
    assert notice["message"].endswith("...") and len(notice["message"]) < 200
    assert world.db.get(Training, world.ids.a.training).discussions[0]["question"] == long_text


def test_an_empty_question_is_refused_and_nobody_is_told(world):
    resp = ask(world, "   ")

    assert resp.status_code == 400 and world.sent == []


def test_every_question_notifies_separately(world):
    ask(world, "first?")
    ask(world, "second?")

    assert [n["message"].split(": ", 1)[1] for n in moderated(world, "training_question_posted")] == ["first?", "second?"]


def test_one_question_is_delivered_once(world):
    resp = ask(world)
    world.sent.clear()
    twn._deliver_question(
        training_id=str(world.ids.a.training), discussion_id=resp.json()["id"], author_name="Lena",
        author_email="l-a@example.com", author_id=str(LEARNER_ID),
    )

    assert world.sent == []  # already claimed by the first delivery


def test_no_enterprise_admin_found_still_saves_the_question(world):
    world.tenant_admins["ids"] = []

    resp = ask(world)

    assert resp.status_code == 201 and world.sent == []
    assert len(world.db.get(Training, world.ids.a.training).discussions) == 1


def test_a_failing_notification_never_breaks_posting_the_question(world, monkeypatch):
    monkeypatch.setattr(twn, "notify_discussion_question", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    resp = ask(world)

    assert resp.status_code == 201
    assert len(world.db.get(Training, world.ids.a.training).discussions) == 1
