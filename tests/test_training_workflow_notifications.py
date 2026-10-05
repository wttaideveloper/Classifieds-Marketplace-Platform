"""Training approval + enrollment notifications on the existing platform feed.

Real SQLite tables for trainings, enrollments, orders and the notification feed. Recipient
resolution runs through the real Event/Training resolvers with only the Invigorate tenant/user
listing stubbed; realtime (Socket.IO `notification`) and push are captured.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import get_current_user
from app.db.database import Base, get_db
from app.models.chat_model import DeviceToken, NotificationPreference
from app.models.enterprise_model import Enterprise
from app.models.notification_model import (
    Notification, NotificationEventLog, NotificationLog, UserNotification,
)
from app.models.training_model import (
    Training, TrainingEnrolment, TrainingLiveSession, TrainingOrder, TrainingWaitlist,
)
from app.services import event_notification_service as recipient_resolvers
from app.services import training_notifications, training_service as service, training_workflow_notifications as twn

PLATFORM_TENANT, TENANT, OTHER_TENANT = uuid4(), uuid4(), uuid4()
SUPER_ADMIN, INACTIVE_SUPER_ADMIN, PLAIN_ADMIN = uuid4(), uuid4(), uuid4()
OWNER, OWNER_2, PROVIDER, MEMBER, OTHER_OWNER = (uuid4() for _ in range(5))
LEARNER = uuid4()
ALL_USERS = [SUPER_ADMIN, INACTIVE_SUPER_ADMIN, PLAIN_ADMIN, OWNER, OWNER_2, PROVIDER, MEMBER, OTHER_OWNER, LEARNER]

TENANT_USERS = {
    PLATFORM_TENANT: [
        {"id": str(SUPER_ADMIN), "isSuperAdmin": True, "status": "active"},
        {"id": str(INACTIVE_SUPER_ADMIN), "isSuperAdmin": True, "status": "inactive"},
        {"id": str(PLAIN_ADMIN), "role": "admin", "status": "active"},
    ],
    TENANT: [
        {"id": str(OWNER), "tenant_role": "tenant_owner", "status": "active"},
        {"id": str(OWNER_2), "tenant_role": "tenant_owner", "status": "active"},
        {"id": str(PROVIDER), "tenant_role": "tenant_admin", "status": "active"},
        {"id": str(MEMBER), "tenant_role": "external_user", "status": "active"},
    ],
    OTHER_TENANT: [{"id": str(OTHER_OWNER), "tenant_role": "tenant_owner", "status": "active"}],
}

super_admin_actor = {"id": str(SUPER_ADMIN), "role": "super_admin", "email": "root@platform.example"}
owner_actor = {"id": str(OWNER), "role": "admin", "email": "owner@acme.example"}
learner_user = {"id": str(LEARNER), "role": "customer", "email": "lena@example.com", "name": "Lena"}


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[m.__table__ for m in (
        Enterprise, Training, TrainingEnrolment, TrainingOrder, TrainingWaitlist, TrainingLiveSession,
        Notification, UserNotification, NotificationLog, NotificationEventLog, DeviceToken, NotificationPreference,
    )])
    sessions = sessionmaker(bind=engine)

    emitted, pushed = [], []

    async def capture_socket_event(user_id, payload):
        emitted.append((user_id, payload))

    def capture_push(tokens, *, title, body, data=None):
        pushed.append({"tokens": tokens, "title": title, "body": body, "data": data})
        return SimpleNamespace(sent_count=len(tokens), credentials_error=None, failures=[])

    monkeypatch.setattr("app.db.database.SessionLocal", sessions)
    monkeypatch.setattr(training_notifications, "_dispatch", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr("app.realtime.emitters.emit_notification", capture_socket_event)
    monkeypatch.setattr("app.services.notification_delivery_service.send_push_to_tokens", capture_push)
    monkeypatch.setattr(recipient_resolvers, "list_tenants", lambda: [{"id": str(t)} for t in TENANT_USERS])
    monkeypatch.setattr(recipient_resolvers, "list_tenant_users", lambda tenant_id: TENANT_USERS.get(tenant_id, []))
    monkeypatch.setattr("app.services.notification_triggers._send_email_via_smtp", lambda *a, **k: True)
    # publishing fans "new training" out to tenant users via the Invigorate API — never from a test
    monkeypatch.setattr("app.services.invigorate_auth_client.list_tenant_user_ids", lambda tenant_id: [])

    with sessions() as db:
        enterprise = Enterprise(
            tenant_id=TENANT, business_short_name="Acme", business_legal_name="Acme Ltd",
            business_email="hello@acme.example", status="approved",
        )
        db.add(enterprise)
        for uid in ALL_USERS:
            db.add(DeviceToken(user_id=uid, token=f"tok-{uid}", platform="android", is_active=True))
        db.commit()
        enterprise_id = enterprise.id

    yield SimpleNamespace(sessions=sessions, enterprise_id=enterprise_id, emitted=emitted, pushed=pushed)
    engine.dispose()


def make_training(w, **overrides):
    values = dict(
        enterprise_id=w.enterprise_id, tenant_id=TENANT, title="Ergonomics 101", category="Wellness",
        status="published", delivery_mode="online", price="0", moderation_history=[],
    )
    values.update(overrides)
    with w.sessions() as db:
        t = Training(**values)
        db.add(t)
        db.commit()
        return t.id


def feed(w, user_id, category=None):
    """Notification rows in this user's feed (what GET /users/me/notifications lists)."""
    with w.sessions() as db:
        q = (
            db.query(Notification)
            .join(UserNotification, UserNotification.notification_id == Notification.id)
            .filter(UserNotification.user_id == user_id)
        )
        if category:
            q = q.filter(Notification.category == category)
        return [
            {"category": n.category, "type": n.notification_type, "title": n.title, "message": n.message,
             "metadata": n.metadata_json, "tenant_id": n.tenant_id}
            for n in q.order_by(Notification.created_at).all()
        ]


def who_got(w, category):
    return {u for u in ALL_USERS if feed(w, u, category)}


def socket_users(w, category):
    return {UUID(uid) for uid, payload in w.emitted if payload["metadata"].get("category") == category}


def pushes_for(w, category):
    return [p for p in w.pushed if p["data"].get("category") == category]


def only_one(items):
    assert len(items) == 1, items
    return items[0]


# --- training approval --------------------------------------------------------------------

def test_submission_goes_to_platform_super_admins_only(world):
    tid = make_training(world, status="draft")
    with world.sessions() as db:
        service.update_training_status_service(db, tid, "pending_approval", owner_actor)

    assert who_got(world, "training_submitted") == {SUPER_ADMIN}  # not inactive/plain admins, owners, providers
    n = only_one(feed(world, SUPER_ADMIN, "training_submitted"))
    assert n["type"] == "automatic" and n["title"] == "Training submitted for approval"
    assert n["metadata"] == {
        "category": "training_submitted", "training_id": str(tid), "entity_type": "training",
        "entity_id": str(tid), "status": "pending_approval",
    }


@pytest.mark.parametrize(
    ("target", "category", "reason"),
    [
        ("approved", "training_approved", None),
        ("rejected", "training_rejected", "Missing syllabus"),
        ("needs_revision", "training_changes_requested", "Please add the agenda"),
    ],
)
def test_decisions_go_to_owning_enterprise_admins_only(world, target, category, reason):
    tid = make_training(world, status="pending_approval")
    with world.sessions() as db:
        service.update_training_status_service(db, tid, target, super_admin_actor, notes=reason)

    assert who_got(world, category) == {OWNER, OWNER_2}  # not providers/members, other tenants, or platform admins
    n = only_one(feed(world, OWNER, category))
    expected = {
        "category": category, "training_id": str(tid), "entity_type": "training",
        "entity_id": str(tid), "status": target,
    }
    if reason:
        expected["reason"] = reason
    assert n["metadata"] == expected
    assert n["tenant_id"] == TENANT


def test_resubmission_notifies_super_admins_again(world):
    tid = make_training(world, status="draft")
    with world.sessions() as db:
        service.update_training_status_service(db, tid, "pending_approval", owner_actor)
        service.update_training_status_service(db, tid, "needs_revision", super_admin_actor, notes="Add agenda")
        service.update_training_status_service(db, tid, "pending_approval", owner_actor)  # resubmit
    assert len(feed(world, SUPER_ADMIN, "training_submitted")) == 2
    assert len(feed(world, OWNER, "training_changes_requested")) == 1


def test_invalid_transition_sends_nothing(world):
    tid = make_training(world, status="draft")
    with world.sessions() as db, pytest.raises(Exception, match="Cannot transition"):
        service.update_training_status_service(db, tid, "approved", super_admin_actor)
    assert world.emitted == [] and world.pushed == []
    with world.sessions() as db:
        assert db.query(Notification).count() == 0


def test_retrying_the_same_transition_notification_does_not_duplicate(world):
    tid = make_training(world, status="draft")
    with world.sessions() as db:
        service.update_training_status_service(db, tid, "pending_approval", owner_actor)
        training = db.get(Training, tid)
        twn.notify_training_approval(training)  # retry / duplicate webhook for the very same transition
        twn.notify_training_approval(training)
    assert len(feed(world, SUPER_ADMIN, "training_submitted")) == 1
    assert len([e for e in world.emitted if e[1]["metadata"]["category"] == "training_submitted"]) == 1


def test_failed_delivery_releases_the_claim_so_a_retry_can_deliver(world, monkeypatch):
    tid = make_training(world, status="draft")
    real = __import__("app.services.notification_service", fromlist=["x"]).create_automatic_notification
    calls = {"n": 0}

    def flaky(db, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("feed unavailable")
        return real(db, **kwargs)

    monkeypatch.setattr("app.services.notification_service.create_automatic_notification", flaky)
    with world.sessions() as db:
        service.update_training_status_service(db, tid, "pending_approval", owner_actor)  # request still succeeds
        assert db.get(Training, tid).status == "pending_approval"
        assert feed(world, SUPER_ADMIN, "training_submitted") == []
        assert db.query(NotificationEventLog).count() == 0  # claim released
        twn.notify_training_approval(db.get(Training, tid))  # retry
    assert len(feed(world, SUPER_ADMIN, "training_submitted")) == 1


def test_no_eligible_recipient_creates_no_orphan_feed_record(world, monkeypatch):
    monkeypatch.setattr(recipient_resolvers, "list_tenant_users", lambda tenant_id: [])
    tid = make_training(world, status="draft")
    with world.sessions() as db:
        service.update_training_status_service(db, tid, "pending_approval", owner_actor)
        assert db.query(Notification).count() == 0 and db.query(NotificationEventLog).count() == 0


# --- realtime + push for the admin-facing events ------------------------------------------

def test_approval_event_is_delivered_in_realtime_and_as_minimal_push(world):
    tid = make_training(world, status="pending_approval")
    with world.sessions() as db:
        service.update_training_status_service(db, tid, "rejected", super_admin_actor, notes="Contains private details")

    assert socket_users(world, "training_rejected") == {OWNER, OWNER_2}
    uid, event = next(e for e in world.emitted if e[0] == str(OWNER))
    assert event["notification_id"] and event["title"] == "Training rejected"
    assert event["metadata"]["reason"] == "Contains private details"  # feed + socket keep the reason

    pushes = pushes_for(world, "training_rejected")
    assert {tuple(p["tokens"]) for p in pushes} == {(f"tok-{OWNER}",), (f"tok-{OWNER_2}",)}
    for p in pushes:
        assert p["data"] == {
            "category": "training_rejected", "training_id": str(tid), "entity_type": "training",
            "entity_id": str(tid), "status": "rejected",
        }
        assert "reason" not in p["data"] and "private" not in p["body"]
        assert all(isinstance(v, str) for v in p["data"].values())


# --- enrollment -> Enterprise Admin -------------------------------------------------------

def _enrol(w, tid, user=learner_user, **payload):
    with w.sessions() as db:
        e = service.create_training_enrol_service(db, tid, payload, current_user=user)
        return e


def _only_enrollment_id(w, tid):
    with w.sessions() as db:
        return db.query(TrainingEnrolment).filter_by(training_id=tid).one().id


def test_automatic_acceptance_notifies_enterprise_admins_once(world):
    tid = make_training(world)
    _enrol(world, tid)
    eid = _only_enrollment_id(world, tid)

    assert who_got(world, "training_enrolled") == {OWNER, OWNER_2}
    n = only_one(feed(world, OWNER, "training_enrolled"))
    assert n["metadata"] == {
        "category": "training_enrolled", "training_id": str(tid), "entity_type": "training",
        "entity_id": str(tid), "enrollment_id": str(eid), "status": "enrolled",
    }
    assert n["message"] == 'A learner enrolled in "Ergonomics 101".'  # no learner name/email
    for p in pushes_for(world, "training_enrolled"):
        assert "Lena" not in p["body"] and "lena@example.com" not in str(p)
    # the learner gets their normal confirmation, not an "accepted" (nobody decided anything)
    assert feed(world, LEARNER, "training_enrollment_accepted") == []
    assert len(feed(world, LEARNER, "training_enrolment_confirmation")) == 1


def test_duplicate_confirmation_for_the_same_enrollment_is_ignored(world):
    tid = make_training(world)
    _enrol(world, tid)
    with world.sessions() as db:
        e = db.query(TrainingEnrolment).filter_by(training_id=tid).one()
        twn.notify_enrollment_confirmed_to_admins(db.get(Training, tid), e)
        twn.notify_enrollment_confirmed_to_admins(db.get(Training, tid), e)
    assert len(feed(world, OWNER, "training_enrolled")) == 1


def test_paid_training_enrolled_only_after_payment_succeeds(world):
    tid = make_training(world, price="499")

    _enrol(world, tid)  # plain enrol records no payment
    assert feed(world, OWNER, "training_enrolled") == []

    with world.sessions() as db:  # checkout = order with confirmed payment, committed with the enrollment
        service.create_training_checkout_service(db, tid, {"participant_name": "Pat", "participant_email": "pat@example.com"})
    assert len(feed(world, OWNER, "training_enrolled")) == 1
    with world.sessions() as db:
        paid = db.query(TrainingEnrolment).filter_by(participant_email="pat@example.com").one()
    assert feed(world, OWNER, "training_enrolled")[0]["metadata"]["enrollment_id"] == str(paid.id)


def test_failed_checkout_sends_nothing(world):
    tid = make_training(world, price="499", status="draft")  # not open -> enrol fails inside checkout
    with world.sessions() as db, pytest.raises(Exception):
        service.create_training_checkout_service(db, tid, {"participant_name": "Pat", "participant_email": "pat@example.com"})
    assert who_got(world, "training_enrolled") == set()


def test_approval_required_enrollment_waits_for_the_admin_decision(world):
    tid = make_training(world, requires_approval=True)
    _enrol(world, tid)
    assert feed(world, OWNER, "training_enrolled") == []  # pending: nothing confirmed yet
    assert len(feed(world, LEARNER, "training_enrolment_confirmation")) == 1  # "awaiting approval"


def test_admin_acceptance_notifies_learner_and_other_admins_but_not_the_actor(world):
    tid = make_training(world, requires_approval=True)
    _enrol(world, tid)
    eid = _only_enrollment_id(world, tid)
    with world.sessions() as db:
        service.approve_training_enrol_service(db, tid, eid, "approve", current_user=owner_actor)

    n = only_one(feed(world, LEARNER, "training_enrollment_accepted"))
    assert n["metadata"] == {
        "category": "training_enrollment_accepted", "training_id": str(tid), "entity_type": "training",
        "entity_id": str(tid), "enrollment_id": str(eid), "status": "enrolled",
    }
    assert socket_users(world, "training_enrollment_accepted") == {LEARNER}
    assert who_got(world, "training_enrolled") == {OWNER_2}  # OWNER did the accepting
    assert only_one(pushes_for(world, "training_enrollment_accepted"))["tokens"] == [f"tok-{LEARNER}"]


def test_rejection_carries_reason_in_feed_and_socket_but_not_in_push(world):
    tid = make_training(world, requires_approval=True)
    _enrol(world, tid)
    eid = _only_enrollment_id(world, tid)
    with world.sessions() as db:
        service.approve_training_enrol_service(db, tid, eid, "reject", reason="Seat reserved for staff", current_user=owner_actor)

    n = only_one(feed(world, LEARNER, "training_enrollment_rejected"))
    assert n["metadata"] == {
        "category": "training_enrollment_rejected", "training_id": str(tid), "entity_type": "training",
        "entity_id": str(tid), "enrollment_id": str(eid), "status": "rejected", "reason": "Seat reserved for staff",
    }
    assert "Seat reserved" not in n["message"]
    _, event = next(e for e in world.emitted if e[0] == str(LEARNER) and e[1]["metadata"]["category"] == "training_enrollment_rejected")
    assert event["metadata"]["reason"] == "Seat reserved for staff"
    push = only_one(pushes_for(world, "training_enrollment_rejected"))
    assert "reason" not in push["data"] and "Seat reserved" not in push["body"]
    assert who_got(world, "training_enrolled") == set()  # a rejection confirms nothing


def test_rejection_without_a_reason_omits_the_key(world):
    tid = make_training(world, requires_approval=True)
    _enrol(world, tid)
    eid = _only_enrollment_id(world, tid)
    with world.sessions() as db:
        service.approve_training_enrol_service(db, tid, eid, "reject", current_user=owner_actor)
    assert "reason" not in only_one(feed(world, LEARNER, "training_enrollment_rejected"))["metadata"]


def test_repeated_status_changes(world):
    tid = make_training(world, requires_approval=True)
    _enrol(world, tid)
    eid = _only_enrollment_id(world, tid)

    def decide(action):
        with world.sessions() as db:
            service.approve_training_enrol_service(db, tid, eid, action, reason="r", current_user=owner_actor)

    decide("approve"); decide("approve")  # retry of the same decision
    assert len(feed(world, LEARNER, "training_enrollment_accepted")) == 1
    assert len(feed(world, OWNER_2, "training_enrolled")) == 1

    decide("reject"); decide("reject")  # admin changes their mind; the retry is silent
    assert len(feed(world, LEARNER, "training_enrollment_rejected")) == 1

    decide("approve")  # a genuinely new decision notifies again
    assert len(feed(world, LEARNER, "training_enrollment_accepted")) == 2
    assert len(feed(world, OWNER_2, "training_enrolled")) == 1  # enrolled-event is once per enrollment

    decide("reject")
    assert len(feed(world, LEARNER, "training_enrollment_rejected")) == 2


def test_paid_approval_required_enrollment_notifies_admins_after_acceptance(world):
    tid = make_training(world, price="499", requires_approval=True)
    with world.sessions() as db:
        service.create_training_checkout_service(db, tid, {"participant_name": "Pat", "participant_email": "pat@example.com"})
        eid = db.query(TrainingEnrolment).filter_by(participant_email="pat@example.com").one().id
    assert feed(world, OWNER, "training_enrolled") == []  # paid, but still awaiting the admin's decision

    with world.sessions() as db:
        service.approve_training_enrol_service(db, tid, eid, "approve", current_user=owner_actor)
    assert who_got(world, "training_enrolled") == {OWNER_2}


def test_waitlist_promotion_to_a_free_training_notifies_admins(world):
    tid = make_training(world, capacity="1")
    _enrol(world, tid)
    first = _only_enrollment_id(world, tid)
    with world.sessions() as db:
        service.join_waitlist_service(db, tid, {"participant_email": "wait@example.com", "participant_name": "Wes"})
        service.cancel_training_enrol_service(db, tid, first, participant_email=learner_user["email"])
        promoted = db.query(TrainingEnrolment).filter_by(participant_email="wait@example.com").one()

    ids = {n["metadata"]["enrollment_id"] for n in feed(world, OWNER, "training_enrolled")}
    assert ids == {str(first), str(promoted.id)}


def test_cancellation_and_refunds_do_not_emit_workflow_types(world):
    tid = make_training(world, price="499")
    with world.sessions() as db:
        service.create_training_checkout_service(db, tid, {"participant_name": "Pat", "participant_email": "pat@example.com"})
        enrolment = db.query(TrainingEnrolment).filter_by(participant_email="pat@example.com").one()
        order = db.query(TrainingOrder).one()
    before = len(world.emitted)

    with world.sessions() as db:
        service.request_training_refund_service(db, tid, order.id, SimpleNamespace(reason="changed my mind", amount=None))
        service.cancel_training_enrol_service(db, tid, enrolment.id, participant_email="pat@example.com")

    new_categories = {p["metadata"]["category"] for _, p in world.emitted[before:]}
    assert not new_categories & {
        "training_enrolled", "training_enrollment_accepted", "training_enrollment_rejected",
        "training_submitted", "training_approved", "training_rejected", "training_changes_requested",
    }
    assert len(feed(world, OWNER, "training_enrolled")) == 1  # the earlier confirmation is not retracted


# --- the existing Web / mobile / Super Admin feed -----------------------------------------

@pytest.fixture
def feed_client(world):
    from app.api.v1.endpoints import user_notification

    app = FastAPI()
    app.include_router(user_notification.router, prefix="/api/v1/users")
    current = {"user": None}

    def database():
        with world.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    client = TestClient(app)

    def as_user(user_id, role):
        current["user"] = {"id": str(user_id), "role": role, "email": f"{user_id}@x.example"}
        return client

    return as_user


def test_events_appear_in_the_existing_feed_for_super_admin_enterprise_admin_and_learner(world, feed_client):
    tid = make_training(world, status="draft", requires_approval=True)
    with world.sessions() as db:
        service.update_training_status_service(db, tid, "pending_approval", owner_actor)
        service.update_training_status_service(db, tid, "approved", super_admin_actor)
        service.update_training_status_service(db, tid, "published", super_admin_actor)
    _enrol(world, tid)
    eid = _only_enrollment_id(world, tid)
    with world.sessions() as db:
        service.approve_training_enrol_service(db, tid, eid, "reject", reason="Full", current_user=owner_actor)

    def items(user_id, role):
        resp = feed_client(user_id, role).get("/api/v1/users/me/notifications")
        assert resp.status_code == 200, resp.text
        return resp.json()["items"]

    assert [i["category"] for i in items(SUPER_ADMIN, "super_admin")] == ["training_submitted"]
    assert {i["category"] for i in items(OWNER, "admin")} == {"training_approved"}
    learner_items = items(LEARNER, "customer")
    rejected = next(i for i in learner_items if i["category"] == "training_enrollment_rejected")
    assert rejected["notification_type"] == "automatic" and rejected["is_read"] is False
    assert rejected["metadata"]["reason"] == "Full" and rejected["metadata"]["enrollment_id"] == str(eid)

    assert items(PROVIDER, "provider") == [] and items(OTHER_OWNER, "admin") == []  # not recipients

    count = feed_client(SUPER_ADMIN, "super_admin").get("/api/v1/users/me/notifications/unread-count").json()
    assert count["unread_count"] == 1
    item_id = items(SUPER_ADMIN, "super_admin")[0]["id"]
    assert feed_client(SUPER_ADMIN, "super_admin").put(f"/api/v1/users/me/notifications/{item_id}/read").status_code == 200
    assert feed_client(SUPER_ADMIN, "super_admin").get("/api/v1/users/me/notifications/unread-count").json()["unread_count"] == 0
