import pytest

from app.services.firebase_push_service import (
    PushSendResult,
    _hint_for_fcm_error,
    _normalize_firebase_credentials_payload,
    get_firebase_diagnostics,
)


def test_normalize_private_key_from_env():
    payload = {
        "private_key": "-----BEGIN PRIVATE KEY-----\\nABC\\n-----END PRIVATE KEY-----\\n"
    }
    normalized = _normalize_firebase_credentials_payload(payload)
    assert "\n" in normalized["private_key"]
    assert "\\n" not in normalized["private_key"]


def test_get_firebase_diagnostics_without_config(monkeypatch):
    monkeypatch.setattr("app.services.firebase_push_service.settings.FIREBASE_CREDENTIALS_PATH", "")
    monkeypatch.setattr("app.services.firebase_push_service.settings.FIREBASE_CREDENTIALS_JSON", "")
    diagnostics = get_firebase_diagnostics()
    assert diagnostics["firebase_configured"] is False
    assert diagnostics["firebase_init_error"]
    hint = _hint_for_fcm_error("SenderIdMismatchError", "SenderId mismatch")
    assert "google-services.json" in hint


def test_hint_for_unregistered():
    hint = _hint_for_fcm_error("UnregisteredError", "Requested entity was not found")
    assert "POST /devices/register" in hint


@pytest.mark.parametrize(
    ("error_code", "message"),
    [
        ("ThirdPartyAuthError", "Auth error from APNS or Web Push Service"),
        ("NotFound", "404 Requested entity was not found"),
    ],
)
def test_hint_for_auth_and_api_errors(error_code, message):
    hint = _hint_for_fcm_error(error_code, message)
    assert hint
