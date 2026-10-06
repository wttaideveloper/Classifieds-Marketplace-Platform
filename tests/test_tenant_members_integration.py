"""Invigorate GET /tenant/members integration (Bearer token) for Event admin recipients."""

from __future__ import annotations

from uuid import uuid4

import pytest
import requests

from app.core.config import settings
from app.db.database import Base
from app.models.notification_model import Notification, NotificationEventLog, NotificationLog, UserNotification
from app.services import event_notification_service as ens
from app.services import invigorate_auth_client as client
from tests.event_sql_support import make_enterprise, make_event, make_session

TOKEN = "tok-secret-123"


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


@pytest.fixture
def workflow_db():
    db = make_session()
    Base.metadata.create_all(
        db.bind,
        tables=[Notification.__table__, UserNotification.__table__, NotificationLog.__table__, NotificationEventLog.__table__],
    )
    try:
        yield db
    finally:
        db.close()


def test_members_url_and_bearer_header_without_tenant_parameter(monkeypatch):
    monkeypatch.setattr(settings, "INVIGORATE_ADMIN_API_BASE_URL", "https://admin.example.test/")
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse({"data": [{"id": "x"}]})

    monkeypatch.setattr(client.requests, "get", fake_get)
    assert client.list_tenant_members(TOKEN) == [{"id": "x"}]
    url, kwargs = calls[0]
    assert url == "https://admin.example.test/api/v1/tenant/members"
    assert kwargs["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    assert kwargs.get("params") is None


def test_members_base_url_falls_back_to_auth_base_url(monkeypatch):
    monkeypatch.setattr(settings, "INVIGORATE_ADMIN_API_BASE_URL", "")
    monkeypatch.setattr(settings, "INVIGORATE_AUTH_BASE_URL", "https://auth.example.test")
    urls = []
    monkeypatch.setattr(client.requests, "get", lambda url, **kw: urls.append(url) or FakeResponse([]))
    client.list_tenant_members(TOKEN)
    assert urls == ["https://auth.example.test/api/v1/tenant/members"]


def test_members_failure_returns_none_and_never_logs_token(monkeypatch, caplog):
    monkeypatch.setattr(settings, "INVIGORATE_ADMIN_API_BASE_URL", "https://admin.example.test")
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: FakeResponse({}, status=404))
    assert client.list_tenant_members(TOKEN) is None
    assert "HTTP 404" in caplog.text and TOKEN not in caplog.text
    assert client.list_tenant_members("") is None


def _event(db, tenant_id, status):
    return make_event(db, tenant_id, enterprise=make_enterprise(db, tenant_id), status=status)


def test_enterprise_admins_resolved_from_members_with_token(workflow_db, monkeypatch):
    tenant = uuid4()
    admin, provider, inactive = uuid4(), uuid4(), uuid4()
    keycloak_sub = uuid4()
    event = _event(workflow_db, tenant, "approved")
    monkeypatch.setattr(ens, "fetch_tenant_me_profile", lambda t: {"id": str(tenant)} if t == TOKEN else None)
    monkeypatch.setattr(
        ens,
        "list_tenant_members",
        lambda t: [
            {"id": str(admin), "keycloak_id": str(keycloak_sub), "tenant_role": "tenant_owner", "status": "active"},
            {"id": str(provider), "tenant_role": "provider", "status": "active"},
            {"id": str(inactive), "tenant_role": "tenant_owner", "status": "disabled"},
        ] if t == TOKEN else None,
    )
    monkeypatch.setattr(ens, "list_tenant_users", lambda _t: pytest.fail("legacy lookup must not be used"))

    recipients, resolved = ens.resolve_enterprise_admin_user_ids(workflow_db, event, access_token=TOKEN)
    assert recipients == [admin] and resolved == tenant
    assert keycloak_sub not in recipients


def test_token_from_another_tenant_is_refused(workflow_db, monkeypatch):
    event = _event(workflow_db, uuid4(), "approved")
    monkeypatch.setattr(ens, "fetch_tenant_me_profile", lambda t: {"id": str(uuid4())})
    monkeypatch.setattr(ens, "list_tenant_members", lambda t: pytest.fail("must not read another tenant's members"))
    recipients, _ = ens.resolve_enterprise_admin_user_ids(workflow_db, event, access_token=TOKEN)
    assert recipients == []


@pytest.mark.parametrize(
    ("status", "ntype"),
    [("approved", "event_approved"), ("rejected", "event_rejected"), ("needs_revision", "event_changes_requested")],
)
def test_admin_transitions_notify_owning_tenant_admin(workflow_db, monkeypatch, status, ntype):
    tenant, admin = uuid4(), uuid4()
    event = _event(workflow_db, tenant, status)
    monkeypatch.setattr(ens, "fetch_tenant_me_profile", lambda t: {"id": str(tenant)})
    monkeypatch.setattr(ens, "list_tenant_members", lambda t: [{"id": str(admin), "tenant_role": "tenant_owner", "status": "active"}])

    async def noop(*_a, **_k):
        return None

    monkeypatch.setattr("app.realtime.emitters.emit_notification", noop)
    row = ens.notify_event_approval_workflow(workflow_db, event, previous_status="pending_approval", access_token=TOKEN)
    assert row.category == ntype and row.metadata_json["event_id"] == str(event.id)
    un = workflow_db.query(UserNotification).filter_by(notification_id=row.id).one()
    assert un.user_id == admin and un.is_read is False

    # Retrying the same transition does not duplicate rows.
    assert ens.notify_event_approval_workflow(workflow_db, event, previous_status="pending_approval", access_token=TOKEN) is None
    assert workflow_db.query(UserNotification).count() == 1


def test_members_api_failure_creates_no_notification(workflow_db, monkeypatch):
    tenant = uuid4()
    event = _event(workflow_db, tenant, "approved")
    monkeypatch.setattr(settings, "INVIGORATE_ADMIN_API_BASE_URL", "https://admin.example.test")
    monkeypatch.setattr(ens, "fetch_tenant_me_profile", lambda t: {"id": str(tenant)})
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: FakeResponse({}, status=500))
    assert ens.notify_event_approval_workflow(workflow_db, event, previous_status="pending_approval", access_token=TOKEN) is None
    assert workflow_db.query(Notification).count() == 0
    assert workflow_db.query(UserNotification).count() == 0


def test_without_token_legacy_lookup_is_unchanged(workflow_db, monkeypatch):
    tenant, admin = uuid4(), uuid4()
    event = _event(workflow_db, tenant, "approved")
    monkeypatch.setattr(ens, "list_tenant_users", lambda t, token=None: [{"id": str(admin), "role": "admin", "status": "active"}])
    monkeypatch.setattr(ens, "list_tenant_members", lambda t: pytest.fail("no token, no members call"))
    recipients, _ = ens.resolve_enterprise_admin_user_ids(workflow_db, event)
    assert recipients == [admin]
