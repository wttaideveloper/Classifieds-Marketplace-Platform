"""training_enrollment_requested: the owning Enterprise Admin(s) hear about an enrollment application the moment the
record is created, including an application that starts as pending_approval.

Built on the same world as test_training_workflow_notifications.py (real SQLite tables, real recipient resolvers,
only the Invigorate listings stubbed). Everything that existed before keeps its own tests there.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import user_notification
from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.models.training_model import TrainingEnrolment, TrainingWaitlist
from app.services import training_service as service
from tests.test_training_workflow_notifications import (  # noqa: F401  (the world fixture is used by name)
    ALL_USERS, LEARNER, OTHER_OWNER, OWNER, OWNER_2, PLAIN_ADMIN, PROVIDER, SUPER_ADMIN, TENANT, TENANT_USERS,
    feed, learner_user, make_training, only_one, owner_actor, pushes_for, who_got, world,
)

CATEGORY = "training_enrollment_requested"


def enrol(w, tid, user=learner_user, **payload):
    with w.sessions() as db:
        return service.create_training_enrol_service(db, tid, payload, current_user=user)


def the_enrollment(w, tid, email="lena@example.com"):
    with w.sessions() as db:
        return db.query(TrainingEnrolment).filter_by(training_id=tid, participant_email=email).one()


# --- when it is sent -----------------------------------------------------------------------------------------

def test_a_pending_approval_application_notifies_the_enterprise_admins_at_once(world):
    tid = make_training(world, requires_approval=True)

    enrol(world, tid)

    assert who_got(world, CATEGORY) == {OWNER, OWNER_2}
    e = the_enrollment(world, tid)
    n = only_one(feed(world, OWNER, CATEGORY))
    assert n["type"] == "automatic" and n["title"] == "New enrollment application"
    assert n["message"] == 'Lena applied to enroll in "Ergonomics 101". It is waiting for your approval.'
    assert n["metadata"] == {
        "category": CATEGORY, "training_id": str(tid), "entity_type": "training", "entity_id": str(tid),
        "enrollment_id": str(e.id), "status": "pending_approval", "training_name": "Ergonomics 101",
        "learner_name": "Lena", "learner_id": str(LEARNER), "source": "application",
    }
    assert n["tenant_id"] == TENANT


def test_the_existing_enrolled_notification_still_waits_for_the_enrollment_to_be_accepted(world):
    tid = make_training(world, requires_approval=True)

    enrol(world, tid)

    assert feed(world, OWNER, "training_enrolled") == []  # unchanged: nothing is confirmed yet
    with world.sessions() as db:
        service.approve_training_enrol_service(db, tid, the_enrollment(world, tid).id, "approve", current_user=owner_actor)
    assert len(feed(world, OWNER_2, "training_enrolled")) == 1       # unchanged: still sent on acceptance
    assert len(feed(world, OWNER_2, CATEGORY)) == 1                    # and the application was not sent again


def test_an_automatically_accepted_enrollment_gets_the_application_and_the_enrolled_notice(world):
    tid = make_training(world)

    enrol(world, tid)

    request = only_one(feed(world, OWNER, CATEGORY))
    assert request["metadata"]["status"] == "enrolled" and "waiting for your approval" not in request["message"]
    assert len(feed(world, OWNER, "training_enrolled")) == 1          # unchanged
    assert len(feed(world, LEARNER, "training_enrolment_confirmation")) == 1  # unchanged


def test_a_paid_training_application_is_announced_before_any_payment_is_recorded(world):
    tid = make_training(world, price="499")

    enrol(world, tid)  # plain enrol: no payment recorded

    assert len(feed(world, OWNER, CATEGORY)) == 1
    assert feed(world, OWNER, "training_enrolled") == []             # unchanged: held back until paid


def test_checkout_announces_the_application_once(world):
    tid = make_training(world, price="499")

    with world.sessions() as db:
        service.create_training_checkout_service(db, tid, {"participant_name": "Pat Paid", "participant_email": "pat@example.com"})

    e = the_enrollment(world, tid, "pat@example.com")
    n = only_one(feed(world, OWNER, CATEGORY))
    assert n["metadata"]["enrollment_id"] == str(e.id) and n["metadata"]["learner_name"] == "Pat Paid"
    assert len(feed(world, OWNER, "training_enrolled")) == 1          # unchanged


def test_an_approval_required_checkout_is_announced_too(world):
    tid = make_training(world, price="499", requires_approval=True)

    with world.sessions() as db:
        service.create_training_checkout_service(db, tid, {"participant_name": "Pat Paid", "participant_email": "pat@example.com"})

    n = only_one(feed(world, OWNER, CATEGORY))
    assert n["metadata"]["status"] == "pending_approval"
    assert feed(world, OWNER, "training_enrolled") == []


def test_a_failed_checkout_announces_nothing(world):
    tid = make_training(world, price="499", status="draft")  # not open for enrolment

    with world.sessions() as db, pytest.raises(Exception):
        service.create_training_checkout_service(db, tid, {"participant_name": "Pat", "participant_email": "pat@example.com"})

    assert who_got(world, CATEGORY) == set()


def test_a_waitlist_promotion_into_the_approval_queue_is_announced_as_such(world):
    tid = make_training(world, requires_approval=True, capacity="1")
    enrol(world, tid, user={"id": str(OTHER_OWNER), "role": "customer", "email": "first@example.com", "name": "First Learner"})
    with world.sessions() as db:  # the training is full: the next learner waits
        service.join_waitlist_service(db, tid, {"participant_email": "lena@example.com", "participant_name": "Lena"}, learner_user)
        first = db.query(TrainingEnrolment).filter_by(participant_email="first@example.com").one()
        first.status = "cancelled"
        db.commit()
        service._promote_waitlist(db, tid)

    promoted = only_one([n for n in feed(world, OWNER, CATEGORY) if n["metadata"]["learner_name"] == "Lena"])
    assert promoted["metadata"]["source"] == "waitlist_promotion" and promoted["metadata"]["status"] == "pending_approval"
    assert promoted["metadata"]["enrollment_id"] == str(the_enrollment(world, tid).id)


# --- who gets it ------------------------------------------------------------------------------------------------

def test_only_the_owning_enterprise_admins_are_recipients(world):
    tid = make_training(world, requires_approval=True)

    enrol(world, tid)

    assert who_got(world, CATEGORY) == {OWNER, OWNER_2}
    for outsider in (SUPER_ADMIN, PLAIN_ADMIN, PROVIDER, OTHER_OWNER, LEARNER):
        assert feed(world, outsider, CATEGORY) == []


def test_an_admin_enrolling_someone_else_is_not_told_about_their_own_action(world):
    tid = make_training(world, requires_approval=True)

    enrol(world, tid, user=owner_actor, participant_email="member@example.com", participant_name="Mia Member")

    assert who_got(world, CATEGORY) == {OWNER_2}
    assert only_one(feed(world, OWNER_2, CATEGORY))["metadata"]["learner_name"] == "Mia Member"


def test_an_admin_who_applies_for_their_own_training_is_not_told_about_themselves(world):
    tid = make_training(world, requires_approval=True)
    admin_learner = {"id": str(OWNER), "role": "admin", "email": "owner@acme.example", "name": "Olive Owner"}

    enrol(world, tid, user=admin_learner)

    assert who_got(world, CATEGORY) == {OWNER_2}


# --- the content ------------------------------------------------------------------------------------------------

def test_the_learner_name_is_never_an_email_address(world):
    tid = make_training(world, requires_approval=True)
    nameless = {"id": str(LEARNER), "role": "customer", "email": "lena@example.com"}  # no name claim: the name falls back to the email

    enrol(world, tid, user=nameless)

    n = only_one(feed(world, OWNER, CATEGORY))
    assert "learner_name" not in n["metadata"] and "@" not in str(n)
    assert n["message"].startswith("A learner applied to enroll")
    for p in pushes_for(world, CATEGORY):
        assert "@" not in str(p)


def test_the_status_in_the_notification_is_the_status_when_it_is_delivered(world, monkeypatch):
    """Delivery reads the enrollment fresh, so a fast admin decision is reflected, not the status at creation."""
    from app.services import training_notifications, training_workflow_notifications as twn

    tid = make_training(world, requires_approval=True)
    queued = []
    monkeypatch.setattr(training_notifications, "_dispatch", lambda fn, *a, **k: queued.append((fn, a, k)))

    enrol(world, tid)
    with world.sessions() as db:
        service.approve_training_enrol_service(db, tid, the_enrollment(world, tid).id, "approve", current_user=owner_actor)
    for fn, a, k in queued:
        fn(*a, **k)

    n = only_one(feed(world, OWNER_2, CATEGORY))
    assert n["metadata"]["status"] == "enrolled"


def test_one_application_is_delivered_once_even_if_dispatched_twice(world):
    from app.services import training_workflow_notifications as twn

    tid = make_training(world, requires_approval=True)
    enrol(world, tid)
    e = the_enrollment(world, tid)

    twn._deliver_requested(training_id=str(tid), enrolment_id=str(e.id), actor_id=None, source="application")

    assert len(feed(world, OWNER, CATEGORY)) == 1


def test_no_enterprise_admin_found_still_creates_the_enrollment(world, monkeypatch):
    from app.services import event_notification_service as recipients

    monkeypatch.setattr(recipients, "list_tenant_users", lambda tenant_id, token=None: [])
    tid = make_training(world, requires_approval=True)

    enrol(world, tid)

    assert who_got(world, CATEGORY) == set() and the_enrollment(world, tid).status == "pending_approval"


def test_a_failing_notification_never_breaks_the_enrollment(world, monkeypatch):
    from app.services import training_workflow_notifications as twn

    monkeypatch.setattr(twn, "notify_enrollment_requested", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    tid = make_training(world, requires_approval=True)

    enrol(world, tid)

    assert the_enrollment(world, tid).status == "pending_approval"
    assert len(feed(world, LEARNER, "training_enrolment_confirmation")) == 1  # the learner's own notice still went


def test_a_group_application_is_one_notification(world):
    tid = make_training(world, requires_approval=True)

    enrol(world, tid, group_enrol=True, group_members=[
        {"name": "Gus", "email": "gus@example.com"}, {"name": "Gail", "email": "gail@example.com"},
    ])

    assert len(feed(world, OWNER, CATEGORY)) == 1


# --- the feed API ----------------------------------------------------------------------------------------------

def test_the_enterprise_admin_reads_it_from_the_existing_feed_endpoint(world):
    tid = make_training(world, requires_approval=True)
    enrol(world, tid)
    app = FastAPI()
    app.include_router(user_notification.router, prefix="/api/v1/users")

    def database():
        with world.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: {"id": str(OWNER), "role": "admin", "email": "owner@acme.example"}

    resp = TestClient(app).get("/api/v1/users/me/notifications")

    assert resp.status_code == 200, resp.text
    [item] = [i for i in resp.json()["items"] if i["category"] == CATEGORY]
    assert item["notification_type"] == "automatic" and item["is_read"] is False
    assert item["metadata"]["enrollment_id"] == str(the_enrollment(world, tid).id)
    assert item["metadata"]["learner_name"] == "Lena" and item["metadata"]["status"] == "pending_approval"
    count = TestClient(app).get("/api/v1/users/me/notifications/unread-count").json()
    assert count["unread_count"] >= 1
