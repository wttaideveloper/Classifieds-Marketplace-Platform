"""Realtime emission from worker threads, and the notification-recipient diagnostics.

Reproduces the production symptom: workflow notifications are created inside sync FastAPI routes
(threadpool) and background threads, where `asyncio.get_event_loop()` raises — so the Socket.IO
`notification` event was silently dropped even though the feed row was saved.
"""
import asyncio
import logging
import threading
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.v1.endpoints import admin as admin_routes
from app.core.config import settings
from app.core.dependencies import get_current_super_admin
from app.db.database import Base, get_db
from app.models.notification_model import Notification, NotificationEventLog, NotificationLog, UserNotification
from app.realtime import loop_bridge
from app.services import event_notification_service as resolvers
from app.services import notification_delivery_service as delivery
from tests.event_sql_support import make_enterprise, make_event, make_session


@pytest.fixture
def server_loop():
    """A real event loop running on its own thread — what the Socket.IO server's loop is."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    loop_bridge.set_main_loop(loop)
    yield loop, thread
    loop_bridge.set_main_loop(None)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=2)
    loop.close()


def test_the_old_pattern_really_fails_in_a_worker_thread():
    outcome = []

    def worker():
        try:
            asyncio.get_event_loop()
            outcome.append("ok")
        except RuntimeError as exc:
            outcome.append(str(exc))

    t = threading.Thread(target=worker)
    t.start(); t.join()
    assert "no current event loop" in outcome[0]


def test_coroutine_from_a_worker_thread_runs_on_the_server_loop(server_loop):
    loop, loop_thread = server_loop
    ran_on, done = [], threading.Event()

    async def emit():
        ran_on.append(threading.get_ident())
        done.set()

    worker = threading.Thread(target=lambda: loop_bridge.run_coroutine(emit()))
    worker.start(); worker.join()
    assert done.wait(2)
    assert ran_on == [loop_thread.ident]  # the loop the Socket.IO server is bound to, not a throwaway one


def test_coroutine_from_async_code_is_scheduled_on_the_running_loop():
    seen = []

    async def emit():
        seen.append("emitted")

    async def main():
        loop_bridge.run_coroutine(emit())
        await asyncio.sleep(0.05)

    asyncio.run(main())
    assert seen == ["emitted"]


def test_without_a_server_loop_the_coroutine_runs_to_completion():
    loop_bridge.set_main_loop(None)
    seen = []

    async def emit():
        seen.append("emitted")

    loop_bridge.run_coroutine(emit())
    assert seen == ["emitted"]


def test_a_failing_emit_never_raises_into_the_caller(server_loop, caplog):
    async def boom():
        raise RuntimeError("redis down")

    with caplog.at_level(logging.ERROR):
        loop_bridge.run_coroutine(boom())
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0.05), server_loop[0]).result(2)
    assert "realtime emit failed" in caplog.text


def _feed_db():
    db = make_session()
    Base.metadata.create_all(db.bind, tables=[Notification.__table__, UserNotification.__table__, NotificationLog.__table__, NotificationEventLog.__table__])
    return db


def test_delivery_from_a_worker_thread_reaches_the_realtime_event(server_loop, monkeypatch):
    """The exact production path: notification rows are written and delivered from a non-loop thread."""
    db = _feed_db()
    recipient = uuid4()
    emitted, done = [], threading.Event()

    async def capture(user_id, payload):
        emitted.append((user_id, payload))
        done.set()

    monkeypatch.setattr("app.realtime.emitters.emit_notification", capture)
    notification = Notification(
        title="t", message="m", notification_type="automatic", category="training_approved",
        delivery_type="immediate", status="processing", metadata_json={"category": "training_approved"},
    )
    db.add(notification); db.commit()

    worker = threading.Thread(target=lambda: delivery.deliver_notification_to_users(
        db, notification_id=notification.id, title="t", body="m", user_ids=[recipient],
        channels=["in_app"], metadata={"category": "training_approved"},
    ))
    worker.start(); worker.join()

    assert done.wait(2)
    assert emitted[0][0] == str(recipient) and emitted[0][1]["metadata"] == {"category": "training_approved"}
    assert db.query(UserNotification).filter_by(user_id=recipient).count() == 1  # row first, event after


# --- diagnostics -------------------------------------------------------------------------

TENANT, PLATFORM_TENANT = uuid4(), uuid4()
SUPER_ADMIN, OWNER, PROVIDER = uuid4(), uuid4(), uuid4()


@pytest.fixture
def diag_env(monkeypatch):
    db = _feed_db()
    enterprise = make_enterprise(db, TENANT)
    event = make_event(db, TENANT, enterprise=enterprise, status="approved")
    users = {
        PLATFORM_TENANT: [{"id": str(SUPER_ADMIN), "isSuperAdmin": True, "status": "active"}],
        TENANT: [
            {"id": str(OWNER), "tenant_role": "tenant_owner", "status": "active"},
            {"id": str(PROVIDER), "tenant_role": "tenant_admin", "status": "active"},
        ],
    }
    monkeypatch.setattr(settings, "INVIGORATE_INTERNAL_API_KEY", "test-key")
    monkeypatch.setattr(resolvers, "list_super_admins", lambda token=None: users[PLATFORM_TENANT])
    monkeypatch.setattr(resolvers, "list_tenant_users", lambda tenant_id, token=None: users.get(tenant_id, []))
    monkeypatch.setattr(delivery, "_emit_realtime_notification", lambda *a, **k: None)

    app = FastAPI()
    app.include_router(admin_routes.router, prefix="/api/v1/admin")
    caller = {"user": {"id": str(SUPER_ADMIN), "role": "super_admin", "status": "active"}}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_super_admin] = lambda: caller["user"]
    return db, event, TestClient(app), caller


def test_diagnostics_show_who_resolves_and_what_was_recorded(diag_env):
    db, event, client, _ = diag_env
    row = resolvers.notify_event_approval_workflow(db, event, previous_status="pending_approval")

    resp = client.get(f"/api/v1/admin/notifications/diagnostics?event_id={event.id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["caller_user_id"] == str(SUPER_ADMIN)
    assert body["config"]["invigorate_internal_api_configured"] is True
    assert body["platform_admins"]["resolved_user_ids"] == [str(SUPER_ADMIN)]
    enterprise = body["event"]["enterprise_admins"]
    assert enterprise["tenant_id"] == str(TENANT)
    assert enterprise["resolved_user_ids"] == [str(OWNER)]
    assert enterprise["excluded"] == {"role_not_enterprise_admin": 1}  # the tenant_admin / provider
    assert enterprise["roles_seen"] == {"tenant_owner": 1, "tenant_admin": 1}
    [recorded] = body["event"]["recorded_notifications"]
    assert recorded["notification_id"] == str(row.id) and recorded["category"] == "event_approved"
    assert recorded["recipient_user_ids"] == [str(OWNER)]


def test_diagnostics_explain_a_missing_internal_api_key(diag_env, monkeypatch):
    db, event, client, _ = diag_env
    monkeypatch.setattr(settings, "INVIGORATE_INTERNAL_API_KEY", "")
    body = client.get(f"/api/v1/admin/notifications/diagnostics?event_id={event.id}").json()
    assert body["config"]["invigorate_internal_api_configured"] is False
    assert body["platform_admins"]["resolved_user_ids"] == []
    assert body["event"]["enterprise_admins"]["resolved_user_ids"] == []
    assert body["event"]["recorded_notifications"] == []


def test_diagnostics_are_refused_to_an_enterprise_admin_fallback_identity(diag_env):
    _, event, client, caller = diag_env
    caller["user"] = {"id": str(OWNER), "role": "admin", "status": "active"}  # admitted by get_current_super_admin's fallback
    assert client.get(f"/api/v1/admin/notifications/diagnostics?event_id={event.id}").status_code == 403


def test_diagnostics_404_for_an_unknown_event(diag_env):
    _, _, client, _ = diag_env
    assert client.get(f"/api/v1/admin/notifications/diagnostics?event_id={uuid4()}").status_code == 404


def test_no_recipients_is_logged_as_a_warning_not_swallowed(diag_env, monkeypatch, caplog):
    db, event, _, _ = diag_env
    monkeypatch.setattr(resolvers, "list_tenant_users", lambda tenant_id, token=None: [])
    with caplog.at_level(logging.WARNING, logger="app.services.event_notification_service"):
        assert resolvers.notify_event_approval_workflow(db, event, previous_status="pending_approval") is None
    assert "NOT sent" in caplog.text and "invigorate_internal_api_configured=True" in caplog.text
    assert db.query(Notification).count() == 0
