from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.platform_notification import router as platform_router
from app.api.v1.endpoints.user_notification import router as user_router
from app.core.dependencies import get_current_user

app = FastAPI()
app.include_router(platform_router, prefix="/notifications")
app.include_router(user_router, prefix="/users")

ADMIN_ID = "550e8400-e29b-41d4-a716-446655440000"
TENANT_ID = "550e8400-e29b-41d4-a716-446655440010"
NOTIF_ID = "550e8400-e29b-41d4-a716-446655440100"


@pytest.fixture
def admin_client():
    app.dependency_overrides[get_current_user] = lambda: {
        "id": ADMIN_ID,
        "role": "admin",
        "tenant_id": TENANT_ID,
    }
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def user_client():
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "550e8400-e29b-41d4-a716-446655440030",
        "role": "customer",
    }
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def provider_client():
    app.dependency_overrides[get_current_user] = lambda: {
        "id": ADMIN_ID,
        "role": "provider",
        "tenant_id": TENANT_ID,
    }
    yield TestClient(app)
    app.dependency_overrides.clear()


@patch("app.services.notification_service.notification_repo")
@patch("app.services.notification_service.deliver_notification_to_users")
@patch("app.services.notification_service.list_tenant_user_ids")
def test_create_notification_delivers_to_tenant_users(
    mock_list_users, mock_deliver, mock_repo, provider_client
):
    """A provider POSTing to /notifications must resolve tenant recipients and
    deliver to them, so the notification becomes visible via GET /me/notifications
    instead of being an orphaned, recipient-less row."""
    recipient_id = uuid4()
    mock_list_users.return_value = [recipient_id]
    mock_deliver.return_value = 1

    created_row = MagicMock(id=NOTIF_ID)
    mock_repo.create_notification.return_value = created_row
    mock_repo.update_notification.return_value = created_row
    mock_repo.get_notification_by_id.return_value = created_row
    mock_repo._map_notification.return_value = {
        "id": NOTIF_ID,
        "tenant_id": TENANT_ID,
        "created_by": ADMIN_ID,
        "title": "Hello",
        "message": "Body",
        "notification_type": "manual",
        "category": "general",
        "delivery_type": "immediate",
        "scheduled_at": None,
        "status": "sent",
        "metadata": {},
        "created_at": "2026-07-21T00:00:00",
        "updated_at": "2026-07-21T00:00:00",
    }

    response = provider_client.post(
        "/notifications",
        json={"title": "Hello", "message": "Body"},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "sent"

    mock_list_users.assert_called_once_with(UUID(TENANT_ID))
    mock_deliver.assert_called_once()
    assert mock_deliver.call_args.kwargs["user_ids"] == [recipient_id]
    assert mock_deliver.call_args.kwargs["notification_id"] == NOTIF_ID


@patch("app.services.notification_service.notification_repo")
@patch("app.services.notification_service.list_tenant_user_ids")
def test_create_notification_no_recipients_raises_400(mock_list_users, mock_repo, provider_client):
    mock_list_users.return_value = []

    response = provider_client.post(
        "/notifications",
        json={"title": "Hello", "message": "Body"},
    )

    assert response.status_code == 400
    mock_repo.create_notification.assert_not_called()


@patch("app.api.v1.endpoints.platform_notification.send_to_users_service")
def test_send_to_users_endpoint(mock_service, admin_client):
    mock_service.return_value = {
        "notification_id": NOTIF_ID,
        "recipients": 2,
        "delivered": 2,
        "status": "sent",
    }
    response = admin_client.post(
        "/notifications/send-to-users",
        json={
            "title": "Hello",
            "message": "Test broadcast",
            "user_ids": [ADMIN_ID],
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "sent"


@patch("app.api.v1.endpoints.user_notification.list_my_notifications_service")
def test_get_my_notifications(mock_service, user_client):
    mock_service.return_value = {
        "items": [],
        "pagination": {"total": 0, "page": 1, "page_size": 20, "total_pages": 0},
    }
    response = user_client.get("/users/me/notifications")
    assert response.status_code == 200
    assert response.json()["items"] == []


@patch("app.api.v1.endpoints.user_notification.my_unread_count_service")
def test_my_unread_count(mock_service, user_client):
    mock_service.return_value = {"unread_count": 3}
    response = user_client.get("/users/me/notifications/unread-count")
    assert response.status_code == 200
    assert response.json()["unread_count"] == 3
