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


@patch("app.api.v1.endpoints.chat_notification.get_preferences_service")
def test_get_notification_preferences(mock_service):
    mock_service.return_value = {
        "email_enabled": True,
        "push_enabled": True,
        "sms_enabled": False,
        "in_app_enabled": True,
        "quiet_hours_start": None,
        "quiet_hours_end": None,
        "updated_at": _NOW,
    }

    response = client.get("/notifications/preferences")

    assert response.status_code == 200
    body = response.json()
    assert body["email_enabled"] is True
    assert body["push_enabled"] is True
    assert body["sms_enabled"] is False


@patch("app.api.v1.endpoints.chat_notification.update_preferences_service")
def test_update_notification_preferences(mock_service):
    mock_service.return_value = {
        "email_enabled": True,
        "push_enabled": False,
        "sms_enabled": True,
        "in_app_enabled": True,
        "quiet_hours_start": None,
        "quiet_hours_end": None,
        "updated_at": _NOW,
    }

    response = client.put(
        "/notifications/preferences",
        json={"email_enabled": True, "push_enabled": False, "sms_enabled": True},
    )

    assert response.status_code == 200
    assert response.json()["sms_enabled"] is True


def test_get_notification_channels():
    response = client.get("/notifications/channels")

    assert response.status_code == 200
    channels = response.json()["channels"]
    providers = {item["channel"]: item["provider"] for item in channels}
    assert providers["Push/App Notifications"] == "Firebase Cloud Messaging (FCM)"
    assert providers["SMS/Text Notifications"] == "Bravo SMS"
