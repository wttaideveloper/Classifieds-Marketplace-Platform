"""Event notification recipients via the documented Invigorate APIs (HTTP mocked, no network).

Response shapes are taken from the Identity API OpenAPI (admin.apis.invigor8.app/openapi.json):
- GET /api/v1/internal/super-admins                 -> data[]: id, keycloakId, isSuperAdmin, status
- GET /api/v1/internal/tenants/{tenant_id}/users    -> data[]: userId, membershipId, role, membershipStatus, userStatus
- GET /api/v1/tenant/members                        -> data[]: id (membership id), userId, role, status
"""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.dependencies import get_current_user
from app.db.database import Base, get_db
from app.models.notification_model import Notification, NotificationEventLog, NotificationLog, UserNotification
from app.services import event_notification_service as ens
from app.services import invigorate_auth_client as client
from tests.event_sql_support import make_enterprise, make_event, make_session

ADMIN_BASE = "https://admin.example.test"
KEY = "internal-key-SECRET"
TOKEN = "bearer-token-SECRET"
BOTH_CREDENTIALS = {"X-Internal-Api-Key": KEY, "Authorization": f"Bearer {TOKEN}"}


class Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class FakeInvigorate:
    """Routes mocked GETs by URL and records every call."""

    def __init__(self):
        self.routes: dict[str, Resp | Exception] = {}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, dict(headers or {})))
        result = self.routes.get(url, Resp({"detail": "not routed"}, 404))
        if isinstance(result, Exception):
            raise result
        return result

    def urls(self):
        return [u for u, _ in self.calls]


@pytest.fixture
def api(monkeypatch):
    fake = FakeInvigorate()
    monkeypatch.setattr(settings, "INVIGORATE_ADMIN_API_BASE_URL", ADMIN_BASE)
    monkeypatch.setattr(settings, "INVIGORATE_AUTH_BASE_URL", ADMIN_BASE)
    monkeypatch.setattr(settings, "INVIGORATE_INTERNAL_API_KEY", KEY)
    monkeypatch.setattr(client.requests, "get", fake)
    return fake


@pytest.fixture
def db():
    session = make_session()
    Base.metadata.create_all(
        session.bind,
        tables=[Notification.__table__, UserNotification.__table__, NotificationLog.__table__, NotificationEventLog.__table__],
    )
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def emitted(monkeypatch):
    sent = []

    async def capture(user_id, payload):
        sent.append((user_id, payload))

    monkeypatch.setattr("app.realtime.emitters.emit_notification", capture)
    return sent


def _event(db, tenant_id, status):
    return make_event(db, tenant_id, enterprise=make_enterprise(db, tenant_id), status=status)


SUPER_ADMINS_URL = f"{ADMIN_BASE}/api/v1/internal/super-admins"
MEMBERS_URL = f"{ADMIN_BASE}/api/v1/tenant/members"
TENANT_ME_URL = f"{ADMIN_BASE}/api/v1/tenant/me"


def _tenant_users_url(tenant_id):
    return f"{ADMIN_BASE}/api/v1/internal/tenants/{tenant_id}/users"


def _inbox(db, user_id):
    from app.api.v1.endpoints import user_notification

    app = FastAPI()
    app.include_router(user_notification.router, prefix="/api/v1/users")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: {"id": str(user_id), "role": "admin"}
    http = TestClient(app)
    items = http.get("/api/v1/users/me/notifications").json()["items"]
    return items, http.get("/api/v1/users/me/notifications/unread-count").json()["unread_count"]


# ---- event_submitted -> Platform Super Admins -------------------------------------------------

def test_event_submitted_notifies_active_super_admins_only(api, db, emitted):
    super_admin, inactive, keycloak_sub, not_flagged = uuid4(), uuid4(), uuid4(), uuid4()
    api.routes[SUPER_ADMINS_URL] = Resp({"message": "ok", "total": 3, "data": [
        {"id": str(super_admin), "keycloakId": str(keycloak_sub), "isSuperAdmin": True, "status": "active", "inviteStatus": "accepted"},
        {"id": str(inactive), "isSuperAdmin": True, "status": "inactive", "inviteStatus": "accepted"},
        {"id": str(not_flagged), "isSuperAdmin": False, "status": "active", "inviteStatus": "accepted"},
    ]})
    event = _event(db, uuid4(), "pending_approval")

    row = ens.notify_event_approval_workflow(db, event, previous_status="draft", access_token=TOKEN)

    # documented internal endpoint with both credentials; the submitter's tenant is never queried
    assert api.urls() == [SUPER_ADMINS_URL]
    assert api.calls[0][1] == BOTH_CREDENTIALS
    assert row.category == "event_submitted"
    assert row.metadata_json["event_id"] == str(event.id) and row.metadata_json["status"] == "pending_approval"
    recipients = db.query(UserNotification).filter_by(notification_id=row.id).all()
    assert [r.user_id for r in recipients] == [super_admin]          # application id, not keycloakId
    assert keycloak_sub not in {r.user_id for r in recipients}
    assert recipients[0].is_read is False and recipients[0].delivered_at is not None
    assert emitted and emitted[0][0] == str(super_admin)

    items, unread = _inbox(db, super_admin)
    assert len(items) == 1 and items[0]["category"] == "event_submitted" and unread == 1

    # idempotent: the same transition never notifies twice
    assert ens.notify_event_approval_workflow(db, event, previous_status="draft", access_token=TOKEN) is None
    assert db.query(UserNotification).count() == 1


# ---- approved / rejected / changes requested -> owning tenant admins -------------------------

@pytest.mark.parametrize(
    ("status", "ntype"),
    [("approved", "event_approved"), ("rejected", "event_rejected"), ("needs_revision", "event_changes_requested")],
)
def test_super_admin_on_another_tenants_event_notifies_the_owning_tenant_admin(api, db, emitted, status, ntype):
    owning_tenant, platform_tenant = uuid4(), uuid4()
    owner_user, owner_membership = uuid4(), uuid4()
    inactive_owner, suspended_owner, tenant_admin, member = uuid4(), uuid4(), uuid4(), uuid4()
    event = _event(db, owning_tenant, status)

    # The Super Admin's token resolves to the PLATFORM tenant, whose members must never be listed.
    api.routes[TENANT_ME_URL] = Resp({"data": {"id": str(platform_tenant)}})
    api.routes[MEMBERS_URL] = Resp({"data": [{"userId": str(uuid4()), "role": "tenant_owner", "status": "active"}]})
    api.routes[_tenant_users_url(owning_tenant)] = Resp({"message": "ok", "total": 5, "data": [
        {"membershipId": str(owner_membership), "userId": str(owner_user), "role": "tenant_owner",
         "membershipStatus": "active", "userStatus": "active", "isSuperAdmin": False},
        {"membershipId": str(uuid4()), "userId": str(inactive_owner), "role": "tenant_owner",
         "membershipStatus": "inactive", "userStatus": "active", "isSuperAdmin": False},
        {"membershipId": str(uuid4()), "userId": str(suspended_owner), "role": "tenant_owner",
         "membershipStatus": "active", "userStatus": "suspended", "isSuperAdmin": False},
        {"membershipId": str(uuid4()), "userId": str(tenant_admin), "role": "tenant_admin",
         "membershipStatus": "active", "userStatus": "active", "isSuperAdmin": False},
        {"membershipId": str(uuid4()), "userId": str(member), "role": "external_user",
         "membershipStatus": "active", "userStatus": "active", "isSuperAdmin": False},
    ]})

    row = ens.notify_event_approval_workflow(
        db, event, previous_status="pending_approval", reason="Add agenda", access_token=TOKEN
    )

    assert MEMBERS_URL not in api.urls()
    users_call = next(c for c in api.calls if c[0] == _tenant_users_url(owning_tenant))
    assert users_call[1] == BOTH_CREDENTIALS
    assert row.category == ntype and row.tenant_id == owning_tenant
    recipients = {r.user_id for r in db.query(UserNotification).filter_by(notification_id=row.id)}
    assert recipients == {owner_user}                 # userId, never membershipId / inactive / other roles
    un = db.query(UserNotification).one()
    assert un.is_read is False and un.delivered_at is not None
    assert emitted[0][0] == str(owner_user)
    items, unread = _inbox(db, owner_user)
    assert items[0]["category"] == ntype and unread == 1

    # idempotent retry of the same transition
    assert ens.notify_event_approval_workflow(
        db, event, previous_status="pending_approval", reason="Add agenda", access_token=TOKEN
    ) is None
    assert db.query(UserNotification).count() == 1


def test_enterprise_admin_in_own_tenant_uses_tenant_members(api, db, emitted):
    tenant, owner_user, membership_id = uuid4(), uuid4(), uuid4()
    event = _event(db, tenant, "approved")
    api.routes[TENANT_ME_URL] = Resp({"data": {"id": str(tenant)}})
    api.routes[MEMBERS_URL] = Resp({"message": "ok", "total": 2, "data": [
        {"id": str(membership_id), "userId": str(owner_user), "role": "tenant_owner", "roleName": "Owner", "status": "active"},
        {"id": str(uuid4()), "userId": str(uuid4()), "role": "tenant_admin", "roleName": "Admin", "status": "active"},
    ]})
    row = ens.notify_event_approval_workflow(db, event, previous_status="pending_approval", access_token=TOKEN)
    assert [r.user_id for r in db.query(UserNotification).filter_by(notification_id=row.id)] == [owner_user]
    assert _tenant_users_url(tenant) not in api.urls()


def test_member_record_without_user_id_never_falls_back_to_membership_or_keycloak_id():
    membership_only = {"id": str(uuid4()), "role": "tenant_owner", "roleName": "Owner", "status": "active"}
    assert client.member_application_user_id(membership_only) is None
    assert client.member_application_user_id({"membershipId": str(uuid4()), "role": "tenant_owner"}) is None
    assert client.member_application_user_id({"keycloakId": str(uuid4()), "keycloak_id": str(uuid4())}) is None


# ---- failures ----------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "failure",
    [Resp({"detail": "forbidden"}, 403), Resp({"detail": "boom"}, 500), requests.ConnectionError("down")],
)
@pytest.mark.parametrize("status", ["pending_approval", "approved"])
def test_invigorate_failure_creates_no_notification_and_leaks_no_secret(api, db, emitted, caplog, failure, status):
    tenant = uuid4()
    event = _event(db, tenant, status)
    api.routes[SUPER_ADMINS_URL] = failure
    api.routes[_tenant_users_url(tenant)] = failure
    with caplog.at_level(logging.WARNING):
        result = ens.notify_event_approval_workflow(db, event, previous_status="draft", access_token=TOKEN)
    assert result is None
    assert db.query(Notification).count() == 0 and db.query(UserNotification).count() == 0 and not emitted
    assert "Invigorate" in caplog.text and "NOT sent" in caplog.text
    assert KEY not in caplog.text and TOKEN not in caplog.text


def test_failed_lookup_does_not_block_the_event_transition(api, db):
    from app.services.event_service import update_event_status_service

    tenant = uuid4()
    event = _event(db, tenant, "pending_approval")
    api.routes[_tenant_users_url(tenant)] = Resp({"detail": "boom"}, 500)
    out = update_event_status_service(
        db, event.id, "approved", {"id": str(uuid4()), "role": "super_admin"}, access_token=TOKEN
    )
    assert out.status == "approved"
    assert db.query(Notification).count() == 0
