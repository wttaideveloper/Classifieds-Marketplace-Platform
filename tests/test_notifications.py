from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.chat_notification import router as notification_router
from app.core.dependencies import get_current_user

app = FastAPI()
app.include_router(notification_router, prefix="/notifications")

_USER_ID = "550e8400-e29b-41d4-a716-446655440000"
_CONV_ID = "550e8400-e29b-41d4-a716-446655440001"
_NOTIF_ID = "550e8400-e29b-41d4-a716-446655440002"
_NOW = datetime.utcnow().isoformat() + "Z"

app.dependency_overrides[get_current_user] = lambda: {
    "id": _USER_ID,
    "role": "provider",
    "email": "provider@example.com",
}

client = TestClient(app)


@patch("app.api.v1.endpoints.chat_notification.mark_notification_read_service")
def test_mark_notification_read(mock_service):
    mock_service.return_value = {
        "id": _NOTIF_ID,
        "notification_type": "chat_message",
        "title": "New message",
        "body": "Hello",
        "data": {"conversation_id": _CONV_ID},
        "is_read": True,
        "created_at": _NOW,
    }

    response = client.patch(f"/notifications/{_NOTIF_ID}/read")

    assert response.status_code == 200
    assert response.json()["is_read"] is True


@patch("app.api.v1.endpoints.chat_notification.mark_all_notifications_read_service")
def test_mark_all_notifications_read(mock_service):
    mock_service.return_value = {"marked_read": 3}

    response = client.patch("/notifications/read-all")

    assert response.status_code == 200
    assert response.json()["marked_read"] == 3


@patch("app.api.v1.endpoints.chat_notification.mark_conversation_notifications_read_service")
def test_mark_conversation_notifications_read(mock_service):
    mock_service.return_value = {
        "conversation_id": _CONV_ID,
        "marked_read": 2,
    }

    response = client.patch(f"/notifications/conversation/{_CONV_ID}/read")

    assert response.status_code == 200
    assert response.json()["marked_read"] == 2


@patch("app.api.v1.endpoints.chat_notification.unread_count_service")
def test_unread_count(mock_service):
    mock_service.return_value = {
        "unread_messages": 2,
        "unread_notifications": 1,
        "total_unread": 3,
    }

    response = client.get("/notifications/unread-count")

    assert response.status_code == 200
    assert response.json()["total_unread"] == 3
