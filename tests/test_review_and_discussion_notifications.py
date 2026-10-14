"""Two notifications on the existing feed (GET /api/v1/users/me/notifications) and the generic Socket.IO event.

1. A learner posts a question in a training discussion -> the owning Enterprise Admin(s):
   training_question_posted {learner_name, training_name, training_id, discussion_id}.
2. A Super Admin moderates or deletes a review (training, product, service or event) -> the owning Enterprise
   Admin(s): review_moderated (approve / reject / pending) and review_deleted, with entity_type, entity_id, review_id,
   previous_status, new_status, action and the acting Super Admin's name. Enterprise Admins and providers cannot
   moderate or delete someone else's review.

Real routes and real SQLite data; the background thread, the identity lookups and the delivery are stubbed.
"""
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import event, product, service, training
from app.core.catalog_access import get_optional_catalog_user
from app.core.dependencies import get_current_user
from app.db.database import Base, get_db
from app.models.event_aux_models import EventFeedback
from app.models.notification_model import NotificationEventLog
from app.models.product_model import ProductReview
from app.models.service_model import ServiceReview
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
    app.include_router(service.router, prefix="/api/v1/services")
    app.include_router(event.router, prefix="/api/v1/events")
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app, raise_server_exceptions=False)

    def as_user(user):
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_optional_catalog_user] = lambda: user
        return client

    as_user(ROOT)   # only a Super Admin moderates
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


def review_of(world, module):
    if module == "training":
        return training_review(world)
    if module == "event":
        return event_review(world)
    model = ProductReview if module == "product" else ServiceReview
    column = model.product_id if module == "product" else model.service_id
    return world.db.query(model).filter(column == getattr(world.ids.a, module)).one()


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
# 2. a Super Admin moderates or deletes a review -> the owning Enterprise Admin(s)
# =============================================================================================================

ITEM_KEY = {"training": "training_id", "product": "product_id", "service": "service_id", "event": "event_id"}
LABEL = {"training": "Training a", "product": "Product a", "service": "Service a", "event": "Event a"}


@pytest.mark.parametrize("module", ["training", "event"])
@pytest.mark.parametrize("action,verb", [("approved", "approved"), ("rejected", "rejected")])
def test_a_status_change_tells_the_owning_enterprise_admins_with_the_full_metadata(world, module, action, verb):
    review_before = review_of(world, module)
    previous, review_id = review_before.moderation_status, str(review_before.id)
    if previous == action:   # the seed already has it there: ask for the other one
        action, verb = ("rejected", "rejected") if action == "approved" else ("approved", "approved")

    assert moderate(world, module, action).status_code == 200

    [notice] = moderated(world)
    assert notice["user_ids"] == ENTERPRISE_ADMINS and notice["channels"] == ["in_app"]   # never the Super Admin
    assert notice["title"] == "Review moderated"
    item_id = str(getattr(world.ids.a, module))
    meta = notice["metadata"]
    assert meta["category"] == "review_moderated"
    assert meta["entity_type"] == module and meta["entity_id"] == item_id and meta[ITEM_KEY[module]] == item_id
    assert meta["review_id"] == review_id
    assert meta["previous_status"] == previous and meta["new_status"] == action and meta["action"] == action
    assert meta["actor_name"] == "Root" and meta["actor_role"] == "super_admin"
    assert meta["item_name"] == LABEL[module]
    assert f'Super Admin Root {verb} a review of "{LABEL[module]}"' in notice["message"]


@pytest.mark.parametrize("module", ["training", "event"])
def test_moving_a_review_to_pending_is_announced(world, module):
    moderate(world, module, "approved")
    world.sent.clear()

    assert moderate(world, module, "pending").status_code == 200

    [notice] = moderated(world)
    assert notice["metadata"]["previous_status"] == "approved" and notice["metadata"]["new_status"] == "pending"
    assert "moved to pending" in notice["message"]


@pytest.mark.parametrize("module", ["training", "event"])
def test_each_change_is_its_own_notification(world, module):
    moderate(world, module, "approved")
    moderate(world, module, "pending")
    moderate(world, module, "rejected")

    assert [(n["metadata"]["previous_status"], n["metadata"]["new_status"]) for n in moderated(world)] == [
        ("pending", "approved"), ("approved", "pending"), ("pending", "rejected"),
    ]


@pytest.mark.parametrize("module", ["training", "event"])
def test_repeating_the_same_status_announces_nothing(world, module):
    moderate(world, module, "approved")
    world.sent.clear()

    assert moderate(world, module, "approved").status_code == 200

    assert moderated(world) == []


@pytest.mark.parametrize("module", ["training", "event"])
@pytest.mark.parametrize("role", ["admin", "provider"])
def test_an_enterprise_admin_or_provider_cannot_moderate_and_nothing_is_announced(world, module, role):
    assert moderate(world, module, "approved", user=staff(role)).status_code == 403

    assert world.sent == [] and review_of(world, module).moderation_status == "pending"


@pytest.mark.parametrize("module", ["training", "event"])
def test_a_super_admin_deleting_tells_the_enterprise_admins(world, module):
    review = review_of(world, module)
    previous, review_id = review.moderation_status, str(review.id)

    assert delete(world, module).status_code == 200

    [notice] = moderated(world, "review_deleted")
    assert notice["user_ids"] == ENTERPRISE_ADMINS and notice["title"] == "Review deleted"
    meta = notice["metadata"]
    assert meta["category"] == "review_deleted" and meta["entity_type"] == module
    assert meta["review_id"] == review_id
    assert meta["previous_status"] == previous and meta["new_status"] == "deleted" and meta["action"] == "deleted"
    assert meta["actor_name"] == "Root" and meta["actor_role"] == "super_admin"
    assert "Super Admin Root deleted a review of" in notice["message"]
    assert moderated(world) == []  # a delete is not also announced as a status change


@pytest.mark.parametrize("module", ["training", "event"])
@pytest.mark.parametrize("role", ["admin", "provider"])
def test_an_enterprise_admin_or_provider_cannot_delete_someone_elses_review(world, module, role):
    assert delete(world, module, user=staff(role)).status_code == 403

    assert world.sent == []


@pytest.mark.parametrize("module", ["training", "event"])
def test_the_author_deleting_their_own_review_is_not_moderation(world, module):
    review = review_of(world, module)
    author = {"id": str(uuid4()), "role": "customer", "email": review.participant_email, "name": "The Author"}

    assert delete(world, module, user=author).status_code == 200

    assert world.sent == []


@pytest.mark.parametrize("module", ["training", "event"])
def test_a_refused_delete_announces_nothing(world, module):
    other_business = staff("admin", B)

    assert delete(world, module, user=other_business).status_code == 403

    assert world.sent == []


@pytest.mark.parametrize("module", ["product", "service"])
def test_product_and_service_reviews_are_announced_the_same_way(world, module):
    kind = "products" if module == "product" else "services"
    review = review_of(world, module)
    action = "rejected" if review.moderation_status != "rejected" else "approved"

    resp = world.client.patch(f"/api/v1/{kind}/{getattr(world.ids.a, module)}/reviews/{review.id}/moderate", json={"action": action})

    assert resp.status_code == 200
    [notice] = moderated(world)
    assert notice["user_ids"] == ENTERPRISE_ADMINS and notice["metadata"][ITEM_KEY[module]] == str(getattr(world.ids.a, module))


def test_a_token_name_that_is_an_email_is_looked_up_instead(world):
    root = dict(ROOT, name="root@example.com")
    world.names[root["id"]] = "Rita Root"

    moderate(world, "training", "approved", user=root)

    [notice] = moderated(world)
    assert notice["metadata"]["actor_name"] == "Rita Root" and "Rita Root" in notice["message"]


def test_no_name_anywhere_still_notifies_without_inventing_one(world):
    root = dict(ROOT, name=None)

    moderate(world, "event", "rejected", user=root)

    [notice] = moderated(world)
    assert "actor_name" not in notice["metadata"]
    assert notice["message"].startswith("A Super Admin rejected")
    assert "@" not in notice["message"]


def test_no_enterprise_admin_found_sends_nothing_and_does_not_break_the_moderation(world):
    world.tenant_admins["ids"] = []

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
        review_id=uuid4(), item_id=world.ids.a.training, item_name="Course", tenant_id=A, previous_status="pending",
        new_status="approved", actor={"id": str(uuid4()), "name": "Rita", "role": "super_admin"}, access_token=None,
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
