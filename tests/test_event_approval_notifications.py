"""Event approval notifications stay on the existing platform notification feed."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user
from app.db.database import Base, get_db
from app.models.notification_model import Notification, NotificationEventLog, NotificationLog, UserNotification
from app.services import event_notification_service
from app.schemas.event_schema import EventCreate
from app.services.event_service import create_event_service, update_event_status_service
from tests.event_sql_support import make_enterprise, make_event, make_session


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


@pytest.mark.parametrize(
    ("status", "expected_type", "reason"),
    [
        ("pending_approval", "event_submitted", None),
        ("approved", "event_approved", None),
        ("rejected", "event_rejected", "Venue capacity is missing"),
        ("needs_revision", "event_changes_requested", "Please add the agenda"),
    ],
)
def test_event_approval_notifications_persist_to_feed_and_emit_generic_socket_event(
    workflow_db, monkeypatch, status, expected_type, reason
):
    tenant_id = uuid4()
    enterprise = make_enterprise(workflow_db, tenant_id)
    event = make_event(workflow_db, tenant_id, enterprise=enterprise, status=status)
    recipient_id = uuid4()
    emitted = []

    monkeypatch.setattr(
        event_notification_service,
        "resolve_platform_admin_user_ids",
        lambda: [recipient_id],
    )
    monkeypatch.setattr(
        event_notification_service,
        "resolve_enterprise_admin_user_ids",
        lambda _db, _event: ([recipient_id], tenant_id),
    )

    async def capture_generic_notification(user_id, payload):
        emitted.append((user_id, payload))

    monkeypatch.setattr("app.realtime.emitters.emit_notification", capture_generic_notification)

    row = event_notification_service.notify_event_approval_workflow(
        workflow_db,
        event,
        previous_status="draft",
        reason=reason,
    )

    assert row.category == expected_type
    assert row.notification_type == "automatic"
    assert row.metadata_json == {
        "category": expected_type,
        "event_id": str(event.id),
        "entity_type": "event",
        "entity_id": str(event.id),
        "status": status,
        **({"reason": reason} if reason else {}),
    }
    assert workflow_db.query(UserNotification).filter_by(notification_id=row.id, user_id=recipient_id).one()
    assert emitted and emitted[0][0] == str(recipient_id)
    assert emitted[0][1]["notification_id"] == str(row.id)
    assert emitted[0][1]["metadata"] == row.metadata_json

    # This is the unchanged Web contract: the same application-user UUID that
    # receives the row can list it and gets the corresponding unread badge.
    from app.api.v1.endpoints import user_notification

    app = FastAPI()
    app.include_router(user_notification.router, prefix="/api/v1/users")
    app.dependency_overrides[get_db] = lambda: workflow_db
    app.dependency_overrides[get_current_user] = lambda: {"id": str(recipient_id), "role": "admin"}
    client = TestClient(app)
    response = client.get("/api/v1/users/me/notifications")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["id"]
    assert item["notification_id"] == str(row.id)
    assert item["user_id"] == str(recipient_id)
    assert item["is_read"] is False and item["delivered_at"]
    assert item["notification_type"] == "automatic"
    assert item["category"] == expected_type
    assert item["metadata"] == row.metadata_json
    assert client.get("/api/v1/users/me/notifications/unread-count").json() == {"unread_count": 1}


def test_event_notification_retry_is_idempotent(workflow_db, monkeypatch):
    tenant_id = uuid4()
    event = make_event(workflow_db, tenant_id, enterprise=make_enterprise(workflow_db, tenant_id), status="pending_approval")
    recipient_id = uuid4()
    monkeypatch.setattr(event_notification_service, "resolve_platform_admin_user_ids", lambda: [recipient_id])

    first = event_notification_service.notify_event_approval_workflow(
        workflow_db, event, previous_status="draft"
    )
    retry = event_notification_service.notify_event_approval_workflow(
        workflow_db, event, previous_status="draft"
    )

    assert first is not None and retry is None
    assert workflow_db.query(Notification).filter_by(category="event_submitted").count() == 1
    assert workflow_db.query(UserNotification).filter_by(user_id=recipient_id).count() == 1


def test_creating_an_event_directly_in_pending_approval_notifies_platform_admins(workflow_db, monkeypatch):
    tenant_id = uuid4()
    enterprise = make_enterprise(workflow_db, tenant_id)
    recipient_id = uuid4()
    monkeypatch.setattr(event_notification_service, "resolve_platform_admin_user_ids", lambda: [recipient_id])
    monkeypatch.setattr(
        "app.services.event_form_config_service.apply_form_configuration_to_event_data",
        lambda *_args: {},
    )

    create_event_service(
        workflow_db,
        EventCreate(
            tenant_id=tenant_id,
            enterprise_id=enterprise.id,
            title="Awaiting review",
            description="A complete event",
            category="Wellness",
            organiser_name="Acme",
            organiser_contact="events@acme.example",
            start_date=datetime.utcnow() + timedelta(days=7),
            end_date=datetime.utcnow() + timedelta(days=8),
            status="pending_approval",
        ),
        {"id": str(uuid4()), "role": "admin", "tenant_id": str(tenant_id)},
    )

    row = workflow_db.query(Notification).filter_by(category="event_submitted").one()
    assert row.metadata_json["event_id"] == row.metadata_json["entity_id"]
    assert row.metadata_json["status"] == "pending_approval"
    assert workflow_db.query(UserNotification).filter_by(notification_id=row.id, user_id=recipient_id).one()


def test_enterprise_recipient_resolution_is_limited_to_the_owning_tenant_admin(workflow_db, monkeypatch):
    owner_tenant = uuid4()
    other_tenant = uuid4()
    enterprise = make_enterprise(workflow_db, owner_tenant)
    event = make_event(workflow_db, owner_tenant, enterprise=enterprise, status="approved")
    enterprise_admin = uuid4()
    provider = uuid4()
    other_tenant_admin = uuid4()
    queried_tenants = []

    def users_for_tenant(tenant_id):
        queried_tenants.append(tenant_id)
        if tenant_id == owner_tenant:
            return [
                {"id": str(enterprise_admin), "tenant_role": "tenant_owner", "status": "active"},
                {"id": str(provider), "tenant_role": "tenant_admin", "status": "active"},
            ]
        return [{"id": str(other_tenant_admin), "role": "admin", "status": "active"}]

    monkeypatch.setattr(event_notification_service, "list_tenant_users", users_for_tenant)

    recipients, resolved_tenant = event_notification_service.resolve_enterprise_admin_user_ids(workflow_db, event)

    assert resolved_tenant == owner_tenant
    assert recipients == [enterprise_admin]
    assert queried_tenants == [owner_tenant]


def test_platform_recipient_resolution_excludes_ordinary_tenant_admins(monkeypatch):
    platform_tenant = uuid4()
    ordinary_tenant = uuid4()
    platform_admin = uuid4()
    tenant_admin = uuid4()

    monkeypatch.setattr(
        event_notification_service,
        "list_tenants",
        lambda: [{"id": str(platform_tenant)}, {"id": str(ordinary_tenant)}],
    )
    monkeypatch.setattr(
        event_notification_service,
        "list_tenant_users",
        lambda tenant_id: (
            [{"id": str(platform_admin), "isSuperAdmin": True, "status": "active"}]
            if tenant_id == platform_tenant
            else [{"id": str(tenant_admin), "role": "admin", "status": "active"}]
        ),
    )

    assert event_notification_service.resolve_platform_admin_user_ids() == [platform_admin]


@pytest.mark.parametrize(
    ("initial_status", "target_status", "reason"),
    [
        ("draft", "pending_approval", None),
        ("pending_approval", "approved", None),
        ("pending_approval", "rejected", "Venue capacity is missing"),
        ("pending_approval", "needs_revision", "Please add the agenda"),
    ],
)
def test_valid_event_approval_transition_triggers_notification_after_commit(
    workflow_db, monkeypatch, initial_status, target_status, reason
):
    tenant_id = uuid4()
    enterprise = make_enterprise(workflow_db, tenant_id)
    event = make_event(workflow_db, tenant_id, enterprise=enterprise, status=initial_status)
    calls = []

    monkeypatch.setattr("app.services.event_template_mapping.validate_event_submission", lambda *_args: None)
    monkeypatch.setattr(
        "app.services.event_notification_service.notify_event_approval_workflow",
        lambda db, changed_event, **kwargs: calls.append((changed_event.status, kwargs)),
    )

    update_event_status_service(
        workflow_db,
        event.id,
        target_status,
        {"id": str(uuid4()), "role": "super_admin"},
        notes=reason,
    )

    assert workflow_db.get(type(event), event.id).status == target_status
    assert len(calls) == 1
    assert calls[0][0] == target_status
    assert calls[0][1]["previous_status"] == initial_status
    assert calls[0][1]["reason"] == reason
    assert calls[0][1]["transition_id"] is not None


def test_failed_event_status_transition_does_not_emit_a_notification(workflow_db, monkeypatch):
    tenant_id = uuid4()
    enterprise = make_enterprise(workflow_db, tenant_id)
    event = make_event(workflow_db, tenant_id, enterprise=enterprise, status="draft")
    emitted = []
    monkeypatch.setattr(
        "app.services.event_notification_service.notify_event_approval_workflow",
        lambda *args, **kwargs: emitted.append((args, kwargs)),
    )

    with pytest.raises(HTTPException, match="Cannot transition"):
        update_event_status_service(workflow_db, event.id, "approved", {"id": str(uuid4()), "role": "super_admin"})

    assert emitted == []
