from unittest.mock import MagicMock, patch

import pytest

from app.services.bravo_sms_service import normalize_phone_number, send_sms
from app.services.chat_notification_service import create_message_notifications


def test_normalize_phone_number_us_10_digit():
    assert normalize_phone_number("5551234567") == "+15551234567"


def test_normalize_phone_number_e164():
    assert normalize_phone_number("+441234567890") == "+441234567890"


def test_normalize_phone_number_invalid():
    assert normalize_phone_number("abc") is None


@patch("app.services.bravo_sms_service.settings")
@patch("app.services.bravo_sms_service.requests.post")
def test_send_sms_bravo(mock_post, mock_settings):
    mock_settings.bravo_sms_configured = True
    mock_settings.BRAVO_SMS_API_URL = "https://bravo.example.com/sms/send"
    mock_settings.BRAVO_API_KEY = "secret"
    mock_settings.BRAVO_SMS_TIMEOUT_SECONDS = 15
    mock_post.return_value = MagicMock(status_code=200)
    mock_post.return_value.raise_for_status = MagicMock()

    assert send_sms(phone="+15551234567", message="Hello", reference_id="user-1") is True
    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs["json"]
    assert payload["to"] == "+15551234567"
    assert payload["message"] == "Hello"
    assert payload["reference_id"] == "user-1"


@patch("app.services.chat_notification_service.send_bravo_sms")
@patch("app.services.chat_notification_service.send_push_to_tokens")
@patch("app.services.chat_notification_service.chat_repo")
def test_create_message_notifications_dispatches_channels(mock_repo, mock_push, mock_sms):
    participant = MagicMock(user_id="550e8400-e29b-41d4-a716-446655440030")
    sender_id = "550e8400-e29b-41d4-a716-446655440020"
    conversation = MagicMock(participants=[participant])
    prefs = MagicMock(
        in_app_enabled=True,
        push_enabled=True,
        email_enabled=False,
        sms_enabled=True,
        sms_phone_number="+15551234567",
    )

    mock_repo.get_conversation_by_id.return_value = conversation
    mock_repo.get_notification_preferences.return_value = prefs
    mock_repo.get_active_device_tokens.return_value = [MagicMock(token="fcm-token-123")]

    create_message_notifications(
        MagicMock(),
        conversation_id="550e8400-e29b-41d4-a716-446655440000",
        sender_id=sender_id,
        message_id="550e8400-e29b-41d4-a716-446655440001",
        title="New message",
        body="Hello",
    )

    mock_repo.create_notification.assert_called_once()
    mock_push.assert_called_once()
    push_data = mock_push.call_args.kwargs["data"]
    assert push_data["type"] == "chat_message"
    assert "conversationId" in push_data
    mock_sms.assert_called_once()
