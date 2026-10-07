"""Training notification recipient resolution: the caller's token reaches the identity lookups, and every
enrolment path saves the learner's application user id (with a safe recovery for rows that lack one)."""
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.core.config import settings
from app.core.dependencies import get_current_super_admin
from app.main import app as fastapi_app
from app.models.training_model import Training, TrainingEnrolment, TrainingOrder, TrainingWaitlist
from app.services import (
    event_notification_service as resolvers,
    invigorate_auth_client as auth_client,
    training_learner_identity as identity,
    training_notifications,
    training_service as service,
    training_workflow_notifications as workflow,
)
from tests.event_sql_support import client_for, reset_overrides
from tests.test_training_workflow_notifications import (  # noqa: F401  (world is a fixture, used by name)
    LEARNER, OWNER, OWNER_2, SUPER_ADMIN, TENANT, TENANT_USERS, feed, learner_user, make_training, owner_actor,
    super_admin_actor, world,
)

LENA = {"id": str(LEARNER), "role": "customer", "email": "lena@example.com", "name": "Lena"}
SOMEONE_ELSE = UUID("7c9e6679-7425-40de-944b-e07fc1f90ae7")  # (an all-zero-prefixed uuid is read back as an int by SQLite)
OWNER_T = {**owner_actor, "tenant_id": str(TENANT)}  # a real Enterprise Admin token carries its tenant


# --- 1. the identity lookups forward the caller's Bearer token ------------------------------------

@pytest.fixture
def http_calls(monkeypatch):
    calls = []

    class Reply:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"items": []}

    monkeypatch.setattr(auth_client.requests, "get", lambda url, headers=None, timeout=None: calls.append((url, headers)) or Reply())
    monkeypatch.setattr(settings, "INVIGORATE_AUTH_BASE_URL", "https://auth.example")
    return calls


@pytest.mark.parametrize(("key", "token", "expected"), [
    ("", "tok", {"Authorization": "Bearer tok"}),                                  # only the caller's token
    ("k", "", {"X-Internal-Api-Key": "k"}),                                       # only the internal key (as before)
    ("k", "tok", {"X-Internal-Api-Key": "k", "Authorization": "Bearer tok"}),     # both
])
def test_tenant_and_user_listings_send_whatever_credentials_exist(monkeypatch, http_calls, key, token, expected):
    monkeypatch.setattr(settings, "INVIGORATE_INTERNAL_API_KEY", key)
    auth_client.list_tenants(token or None)
    auth_client.list_tenant_users(uuid4(), token or None)
    assert [headers for _, headers in http_calls] == [expected, expected]


def test_with_no_credentials_at_all_nothing_is_requested(monkeypatch, http_calls):
    monkeypatch.setattr(settings, "INVIGORATE_INTERNAL_API_KEY", "")
    assert auth_client.list_tenants() == [] and auth_client.list_tenant_users(uuid4()) == []
    assert http_calls == []


# --- 2. the token travels from the handler to the recipient lookups -------------------------------

@pytest.fixture
def seen(monkeypatch, world):
    """Resolvers that record the token they were handed (and still return the shared test fixtures)."""
    tokens = {"tenants": [], "users": []}

    def tenants(access_token=None):
        tokens["tenants"].append(access_token)
        return [{"id": str(t)} for t in TENANT_USERS]

    def users(tenant_id, access_token=None):
        tokens["users"].append(access_token)
        return TENANT_USERS.get(tenant_id, [])

    monkeypatch.setattr(resolvers, "list_tenants", tenants)
    monkeypatch.setattr(resolvers, "list_tenant_users", users)
    return tokens


def test_submission_looks_up_super_admins_with_the_callers_token(world, seen):
    tid = make_training(world, status="draft")
    with world.sessions() as db:
        service.update_training_status_service(db, tid, "pending_approval", owner_actor, access_token="tok-owner")
    assert seen["tenants"] and set(seen["tenants"]) == {"tok-owner"} and set(seen["users"]) == {"tok-owner"}
    assert feed(world, SUPER_ADMIN, "training_submitted")


@pytest.mark.parametrize(("target", "category"), [
    ("approved", "training_approved"), ("rejected", "training_rejected"), ("needs_revision", "training_changes_requested"),
])
def test_decisions_look_up_enterprise_admins_with_the_callers_token(world, seen, target, category):
    tid = make_training(world, status="pending_approval")
    with world.sessions() as db:
        service.update_training_status_service(db, tid, target, super_admin_actor, notes="why", access_token="tok-root")
    assert seen["users"] and set(seen["users"]) == {"tok-root"}
    assert {OWNER, OWNER_2} == {u for u in (OWNER, OWNER_2) if feed(world, u, category)}


def test_training_enrolled_looks_up_enterprise_admins_with_the_enrolling_token(world, seen):
    tid = make_training(world)
    with world.sessions() as db:
        service.create_training_enrol_service(db, tid, {}, current_user=LENA, access_token="tok-lena")
    assert set(seen["users"]) == {"tok-lena"} and feed(world, OWNER, "training_enrolled")


def test_without_a_token_the_lookups_are_called_exactly_as_before(world, seen):
    tid = make_training(world, status="draft")
    with world.sessions() as db:
        service.update_training_status_service(db, tid, "pending_approval", owner_actor)
    assert set(seen["tenants"]) == {None} and set(seen["users"]) == {None}


@pytest.fixture
def api(world, monkeypatch):
    """The real app, authenticated, with the notification entry points replaced by spies."""
    calls = []
    monkeypatch.setattr(workflow, "notify_training_approval", lambda t, **kw: calls.append(("approval", t.status, kw.get("access_token"))))
    monkeypatch.setattr(training_notifications, "notify_new_training", lambda t, **kw: calls.append(("new", t.status, kw.get("access_token"))))
    monkeypatch.setattr(workflow, "notify_enrollment_confirmed_to_admins", lambda t, e, **kw: calls.append(("enrolled", e.status, kw.get("access_token"))))

    def client(user, super_admin=None):
        c = client_for(world.sessions(), user)
        fastapi_app.dependency_overrides[get_current_super_admin] = lambda: super_admin or super_admin_actor
        return c

    yield SimpleNamespace(client=client, calls=calls)
    reset_overrides()


@pytest.mark.parametrize(("prefix", "action", "payload", "start", "status"), [
    ("/api/v1/admin/trainings", "approve", None, "pending_approval", "approved"),
    ("/api/v1/admin/trainings", "reject", {"reason": "no"}, "pending_approval", "rejected"),
    ("/api/v1/admin/trainings", "request-changes", {"reason": "fix"}, "pending_approval", "needs_revision"),
    ("/api/v1/admin/courses", "reject", {"reason": "no"}, "pending_approval", "rejected"),   # the Course alias too
    ("/api/v1/admin/courses", "approve", None, "pending_approval", "approved"),
])
def test_admin_decision_handlers_pass_the_bearer_token_on(world, api, prefix, action, payload, start, status):
    tid = make_training(world, status=start)
    resp = api.client(super_admin_actor).post(
        f"{prefix}/{tid}/{action}", json=payload, headers={"Authorization": "Bearer tok-bearer"},
    )
    assert resp.status_code == 200, resp.text
    assert api.calls == [("approval", status, "tok-bearer")]


def test_the_session_cookie_token_is_forwarded_too(world, api):
    tid = make_training(world, status="pending_approval")
    client = api.client(super_admin_actor)
    client.cookies.set("access_token", "tok-cookie")
    assert client.post(f"/api/v1/admin/trainings/{tid}/reject", json={"reason": "no"}).status_code == 200
    assert api.calls == [("approval", "rejected", "tok-cookie")]


def test_publish_and_status_and_resubmit_handlers_pass_the_token_on(world, api):
    headers = {"Authorization": "Bearer tok-b"}
    tid = make_training(world, status="approved")
    resp = api.client(OWNER_T).post(f"/api/v1/trainings/{tid}/publish", headers=headers)
    assert resp.status_code == 200, resp.text
    assert ("new", "published", "tok-b") in api.calls

    with world.sessions() as db:
        t = db.get(Training, tid)
        t.status = "needs_revision"
        db.commit()
    assert api.client(OWNER_T).post(f"/api/v1/trainings/{tid}/resubmit", headers=headers).status_code == 200
    assert ("approval", "pending_approval", "tok-b") in api.calls


def test_enrolment_handlers_pass_the_token_on(world, api):
    tid = make_training(world, requires_approval=True)
    learner_headers = {"Authorization": "Bearer tok-learner"}
    assert api.client(LENA).post(f"/api/v1/trainings/{tid}/enrol", json={}, headers=learner_headers).status_code == 201
    with world.sessions() as db:
        eid = db.query(TrainingEnrolment).filter_by(training_id=tid).one().id
    resp = api.client(OWNER_T).post(
        f"/api/v1/trainings/{tid}/enrolments/{eid}/approve", json={"action": "approve"}, headers={"Authorization": "Bearer tok-admin"},
    )
    assert resp.status_code == 200, resp.text
    assert ("enrolled", "enrolled", "tok-admin") in api.calls


# --- 3. the learner's user id is saved on every enrolment path ------------------------------------

@pytest.fixture
def profile(monkeypatch):
    """The Auth service's /auth/me for the caller's token."""
    auth_client._ROLES_CACHE.clear()
    state = {"response": None}
    monkeypatch.setattr(auth_client, "fetch_auth_me_profile", lambda token: state["response"])
    yield state
    auth_client._ROLES_CACHE.clear()


def enrol(world, user, tid, token="t", **payload):
    with world.sessions() as db:
        service.create_training_enrol_service(db, tid, payload, current_user=user, access_token=token)


def row(world, email):
    with world.sessions() as db:
        return db.query(TrainingEnrolment).filter(TrainingEnrolment.participant_email == email).one()


def test_direct_enrolment_saves_the_callers_user_id(world, profile):
    tid = make_training(world)
    enrol(world, LENA, tid)
    assert row(world, "lena@example.com").user_id == LEARNER


def test_email_case_or_spelling_in_the_token_does_not_lose_the_user_id(world, profile):
    tid = make_training(world)
    enrol(world, {**LENA, "email": "Lena@Example.COM"}, tid, participant_email="lena@example.com")
    assert row(world, "lena@example.com").user_id == LEARNER


def test_a_token_without_an_email_claim_is_resolved_from_the_auth_profile(world, profile):
    tid = make_training(world)
    profile["response"] = {"data": {"email": "lena@example.com", "emailVerified": True}}
    enrol(world, {"id": str(LEARNER), "role": "customer"}, tid, participant_email="Lena@example.com")
    assert row(world, "Lena@example.com").user_id == LEARNER


def test_a_different_email_in_the_body_is_not_attributed_to_the_caller(world, profile):
    tid = make_training(world)
    profile["response"] = {"data": {"email": "lena@example.com", "emailVerified": True}}
    enrol(world, LENA, tid, participant_email="friend@example.com")
    assert row(world, "friend@example.com").user_id is None


def test_staff_enrolling_someone_else_does_not_stamp_their_own_id(world, profile):
    tid = make_training(world)
    enrol(world, owner_actor, tid, participant_email="walkin@example.com", participant_name="Walk In")
    assert row(world, "walkin@example.com").user_id is None
    enrol(world, {**owner_actor, "email": "owner@example.com"}, tid, participant_email="owner@example.com")
    assert row(world, "owner@example.com").user_id == UUID(owner_actor["id"])  # staff enrolling themselves is fine


def test_a_learner_with_no_verifiable_email_is_still_treated_as_enrolling_themselves(world, profile):
    tid = make_training(world)
    enrol(world, {"id": str(LEARNER), "role": "customer"}, tid, participant_email="lena@example.com")
    assert row(world, "lena@example.com").user_id == LEARNER


def test_checkout_saves_the_user_id(world, profile):
    tid = make_training(world, price="499")
    with world.sessions() as db:
        service.create_training_checkout_service(
            db, tid, {"participant_name": "Lena", "participant_email": "lena@example.com"}, current_user=LENA, access_token="t",
        )
        assert db.query(TrainingOrder).count() == 1
    assert row(world, "lena@example.com").user_id == LEARNER


def test_joining_the_waitlist_saves_it_and_promotion_carries_it_over(world, profile):
    tid = make_training(world, capacity="1")
    enrol(world, {"id": str(uuid4()), "role": "customer", "email": "first@example.com"}, tid)
    second = {"id": str(uuid4()), "role": "customer", "email": "second@example.com"}
    with world.sessions() as db:
        entry = service.join_waitlist_service(db, tid, {}, second, access_token="t")
        assert db.get(TrainingWaitlist, UUID(entry["id"])).user_id == UUID(second["id"])
        first = db.query(TrainingEnrolment).filter_by(participant_email="first@example.com").one()
        service.cancel_training_enrol_service(db, tid, first.id, participant_email="first@example.com", access_token="t")
    assert row(world, "second@example.com").user_id == UUID(second["id"])


def test_an_older_waitlist_entry_without_an_id_is_recovered_when_promoted(world, profile):
    tid = make_training(world, capacity="1")
    other = make_training(world, capacity=None)  # unused id helper returns the same default id, so seed rows directly
    enrol(world, {"id": str(uuid4()), "role": "customer", "email": "first@example.com"}, tid)
    with world.sessions() as db:
        db.add(TrainingEnrolment(training_id=tid, participant_name="Old", participant_email="old@example.com",
                                 status="cancelled", user_id=LEARNER, qr_code=uuid4().hex[:12]))
        db.add(TrainingWaitlist(training_id=tid, participant_name="Old", participant_email="Old@Example.com"))  # no user_id
        db.commit()
        first = db.query(TrainingEnrolment).filter_by(participant_email="first@example.com").one()
        service.cancel_training_enrol_service(db, tid, first.id, participant_email="first@example.com")
    promoted = row(world, "Old@Example.com")
    assert promoted.user_id == LEARNER and promoted.status == "enrolled"


# --- 4. existing rows with no user id -------------------------------------------------------------

def seed_enrolment(world, tid, email, status="pending_approval", user_id=None):
    with world.sessions() as db:
        e = TrainingEnrolment(training_id=tid, participant_name="X", participant_email=email, status=status,
                              user_id=user_id, qr_code=uuid4().hex[:12])
        db.add(e)
        db.commit()
        return e.id


def decide(world, tid, eid, action, actor=owner_actor, token=None):
    with world.sessions() as db:
        service.approve_training_enrol_service(db, tid, eid, action, reason="r", current_user=actor, access_token=token)


def test_a_decision_on_an_old_row_finds_the_learner_from_their_other_enrolments(world):
    tid = make_training(world, requires_approval=True)
    seed_enrolment(world, tid, "Lena@Example.com", status="cancelled", user_id=LEARNER)  # a newer row that has the id
    eid = seed_enrolment(world, tid, "lena@example.com")                               # the old row without one
    decide(world, tid, eid, "approve")
    assert feed(world, LEARNER, "training_enrollment_accepted")
    with world.sessions() as db:
        assert db.get(TrainingEnrolment, eid).user_id == LEARNER  # healed, not just notified


def test_a_rejection_reaches_a_learner_recovered_the_same_way(world):
    tid = make_training(world, requires_approval=True)
    seed_enrolment(world, tid, "lena@example.com", status="cancelled", user_id=LEARNER)
    eid = seed_enrolment(world, tid, "lena@example.com")
    decide(world, tid, eid, "reject")
    [n] = feed(world, LEARNER, "training_enrollment_rejected")
    assert n["metadata"]["reason"] == "r"


def test_a_decision_can_find_the_learner_in_the_tenants_member_list(world, monkeypatch):
    tid = make_training(world, requires_approval=True)
    eid = seed_enrolment(world, tid, "member@example.com")
    members = [{"id": str(LEARNER), "email": "Member@Example.com"}, {"id": str(uuid4()), "email": "else@example.com"}]
    monkeypatch.setattr(resolvers, "list_tenant_users", lambda tenant_id, access_token=None: TENANT_USERS[TENANT] + members if tenant_id == TENANT else [])
    decide(world, tid, eid, "approve", token="tok-admin")
    assert feed(world, LEARNER, "training_enrollment_accepted")


def test_an_unrecoverable_learner_is_left_alone_and_the_decision_still_succeeds(world):
    tid = make_training(world, requires_approval=True)
    eid = seed_enrolment(world, tid, "nobody@example.com")
    decide(world, tid, eid, "approve")
    with world.sessions() as db:
        row_ = db.get(TrainingEnrolment, eid)
        assert row_.status == "enrolled" and row_.user_id is None  # nothing guessed
    assert not any(feed(world, u, "training_enrollment_accepted") for u in (LEARNER, OWNER, OWNER_2))


def test_only_the_same_email_is_ever_matched(world):
    tid = make_training(world, requires_approval=True)
    seed_enrolment(world, tid, "lena.other@example.com", status="cancelled", user_id=LEARNER)
    eid = seed_enrolment(world, tid, "lena@example.com")
    decide(world, tid, eid, "approve")
    with world.sessions() as db:
        assert db.get(TrainingEnrolment, eid).user_id is None


def test_my_enrolments_links_the_learners_own_old_rows(world, profile):
    from app.models.training_model import TrainingProgress

    TrainingProgress.__table__.create(bind=world.sessions.kw["bind"], checkfirst=True)  # the list view reads progress
    tid = make_training(world)
    mine = seed_enrolment(world, tid, "LENA@example.com", status="enrolled")
    mine_waiting = None
    with world.sessions() as db:
        w = TrainingWaitlist(training_id=tid, participant_name="L", participant_email="lena@example.com")
        db.add(w)
        db.commit()
        mine_waiting = w.id
    theirs = seed_enrolment(world, tid, "someone.else@example.com", status="enrolled")
    already = seed_enrolment(world, tid, "lena@example.com", status="cancelled", user_id=SOMEONE_ELSE)

    client = client_for(world.sessions(), LENA)
    try:
        assert client.get("/api/v1/trainings/my/enrolments", headers={"Authorization": "Bearer t"}).status_code == 200
    finally:
        reset_overrides()
    with world.sessions() as db:
        assert db.get(TrainingEnrolment, mine).user_id == LEARNER
        assert db.get(TrainingWaitlist, mine_waiting).user_id == LEARNER
        assert db.get(TrainingEnrolment, theirs).user_id is None                # another person's row
        assert db.get(TrainingEnrolment, already).user_id == SOMEONE_ELSE        # never overwritten


def test_an_unverified_email_links_nothing(world, profile):
    tid = make_training(world)
    eid = seed_enrolment(world, tid, "lena@example.com", status="enrolled")
    profile["response"] = {"data": {"email": "lena@example.com", "emailVerified": False}}
    with world.sessions() as db:
        assert identity.link_caller_enrolments(db, LENA, "t") == 0
        assert db.get(TrainingEnrolment, eid).user_id is None


def test_the_backfill_reports_by_default_and_writes_only_when_asked(world):
    tid = make_training(world)
    seed_enrolment(world, tid, "lena@example.com", status="cancelled", user_id=LEARNER)
    fixable = seed_enrolment(world, tid, "LENA@example.com")
    seed_enrolment(world, tid, "nobody@example.com")
    with world.sessions() as db:
        assert identity.backfill_user_ids(db) == {"checked": 2, "resolved": 1, "unresolved": 1, "written": 0}
        assert db.get(TrainingEnrolment, fixable).user_id is None
        assert identity.backfill_user_ids(db, apply=True) == {"checked": 2, "resolved": 1, "unresolved": 1, "written": 1}
        assert db.get(TrainingEnrolment, fixable).user_id == LEARNER
        assert identity.backfill_user_ids(db, apply=True)["checked"] == 1  # idempotent: only the unresolvable row remains
