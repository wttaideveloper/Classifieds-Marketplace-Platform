"""POST /api/v1/events/{id}/resubmit notifies the Platform Super Admins.

Drives the real route, with the Invigorate tenant/user listings stubbed, and reads the result back through
GET /api/v1/users/me/notifications exactly as a Super Admin's client would.
"""
import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db.database import Base
from app.models.notification_model import Notification, NotificationLog, UserNotification
from app.services import event_notification_service as resolvers
from tests.event_sql_support import (
    API, client_for, make_enterprise, make_event, make_session, reset_overrides, staff_user,
)

PLATFORM_TENANT, TENANT = uuid4(), uuid4()
SUPER_ADMIN, INACTIVE_SUPER_ADMIN, OWNER = uuid4(), uuid4(), uuid4()


@pytest.fixture
def world(monkeypatch):
    for target in (
        "app.services.notification_triggers._safe_notify",
        "app.services.super_admin_identity.fetch_internal_user_by_id",
        "app.services.super_admin_identity.fetch_auth_me_profile",
        "app.services.invigorate_auth_client.fetch_tenant_me_profile",
        "app.services.invigorate_auth_client.fetch_auth_me_profile",
    ):
        monkeypatch.setattr(target, lambda *a, **k: None)
    monkeypatch.setattr("app.services.event_template_mapping.validate_event_submission", lambda *a, **k: None)

    db = make_session()
    Base.metadata.create_all(db.bind, tables=[Notification.__table__, UserNotification.__table__, NotificationLog.__table__])
    enterprise = make_enterprise(db, TENANT)

    seen = {"tenants": [], "users": []}
    users = {
        PLATFORM_TENANT: [
            {"id": str(SUPER_ADMIN), "isSuperAdmin": True, "status": "active"},
            {"id": str(INACTIVE_SUPER_ADMIN), "isSuperAdmin": True, "status": "inactive"},
        ],
        TENANT: [{"id": str(OWNER), "tenant_role": "tenant_owner", "status": "active"}],
    }

    def list_tenants(access_token=None):
        seen["tenants"].append(access_token)
        return [{"id": str(t)} for t in users]

    def list_tenant_users(tenant_id, access_token=None):
        seen["users"].append(access_token)
        return users.get(tenant_id, [])

    monkeypatch.setattr(resolvers, "list_tenants", list_tenants)
    monkeypatch.setattr(resolvers, "list_tenant_users", list_tenant_users)

    emitted = []

    async def capture(user_id, payload):
        emitted.append((user_id, payload))

    monkeypatch.setattr("app.realtime.emitters.emit_notification", capture)

    owner = staff_user(TENANT, "admin")
    owner["id"] = str(OWNER)
    yield SimpleNamespace(db=db, enterprise=enterprise, owner=owner, seen=seen, users=users, emitted=emitted)
    reset_overrides()
    db.close()


def event_in(world, status="needs_revision"):
    return make_event(world.db, TENANT, enterprise=world.enterprise, status=status)


def resubmit(world, event, headers=None):
    return client_for(world.db, world.owner).post(f"{API}/{event.id}/resubmit", headers=headers or {})


def super_admin_feed(world):
    """What the Super Admin's own client gets from GET /users/me/notifications."""
    resp = client_for(world.db, {"id": str(SUPER_ADMIN), "role": "super_admin", "email": "root@platform.example"}).get(
        "/api/v1/users/me/notifications"
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


def test_resubmit_creates_event_submitted_for_the_super_admin(world):
    event = event_in(world)
    resp = resubmit(world, event)
    assert resp.status_code == 200 and resp.json()["status"] == "pending_approval"

    [item] = super_admin_feed(world)
    assert item["category"] == "event_submitted"
    assert item["notification_type"] == "automatic"
    assert item["title"] == "Event submitted for approval"
    assert item["is_read"] is False
    assert item["metadata"]["event_id"] == str(event.id)
    assert item["metadata"] == {
        "event_id": str(event.id), "entity_type": "event", "entity_id": str(event.id),
        "status": "pending_approval", "reason": None,
    }


def test_the_realtime_event_is_emitted_after_the_row_exists(world):
    event = event_in(world)
    resubmit(world, event)
    [(user_id, payload)] = [e for e in world.emitted if e[0] == str(SUPER_ADMIN)]
    assert payload["metadata"]["event_id"] == str(event.id)
    row = world.db.query(UserNotification).filter_by(user_id=SUPER_ADMIN).one()
    assert payload["notification_id"] == str(row.notification_id)


def test_only_active_super_admins_receive_it(world):
    resubmit(world, event_in(world))
    recipients = {str(r.user_id) for r in world.db.query(UserNotification).all()}
    assert recipients == {str(SUPER_ADMIN)}  # not the inactive Super Admin, not the Enterprise Admin who resubmitted


def test_every_resubmission_notifies_again(world):
    event = event_in(world)
    resubmit(world, event)
    # the Super Admin asks for changes again, and the Enterprise Admin resubmits a second time
    event.status = "needs_revision"
    world.db.commit()
    resubmit(world, event)
    assert [i["category"] for i in super_admin_feed(world)] == ["event_submitted", "event_submitted"]


def test_a_draft_can_be_submitted_through_the_same_route(world):
    resp = resubmit(world, event_in(world, "draft"))
    assert resp.status_code == 200 and [i["category"] for i in super_admin_feed(world)] == ["event_submitted"]


@pytest.mark.parametrize("status", ["rejected", "published", "pending_approval", "cancelled"])
def test_other_statuses_are_refused_and_notify_nobody(world, status):
    resp = resubmit(world, event_in(world, status))
    assert resp.status_code == 400 and "needs_revision or draft" in resp.json()["detail"]
    assert world.db.query(Notification).count() == 0


def test_the_callers_bearer_token_reaches_the_super_admin_lookup(world):
    resubmit(world, event_in(world), headers={"Authorization": "Bearer tok-owner"})
    assert world.seen["tenants"] and set(world.seen["tenants"]) == {"tok-owner"}
    assert set(world.seen["users"]) == {"tok-owner"}


def test_without_a_token_the_lookup_is_made_as_before(world):
    resubmit(world, event_in(world))
    assert set(world.seen["tenants"]) == {None}


def test_when_no_super_admin_can_be_found_the_resubmit_succeeds_but_logs_why(world, caplog):
    world.users[PLATFORM_TENANT] = []  # e.g. the Invigorate listing is empty or unreachable
    with caplog.at_level(logging.WARNING, logger="app.services.event_notification_service"):
        resp = resubmit(world, event_in(world))
    assert resp.status_code == 200 and resp.json()["status"] == "pending_approval"
    assert world.db.query(Notification).count() == 0
    assert "event_submitted notification NOT sent" in caplog.text
