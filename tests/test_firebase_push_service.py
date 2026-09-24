import pytest

from app.services.firebase_push_service import (
    _hint_for_fcm_error,
    _load_firebase_credentials_payload,
    _normalize_firebase_credentials_payload,
    get_firebase_diagnostics,
    send_push_to_tokens,
)


def test_normalize_private_key_from_env():
    payload = {
        "private_key": "-----BEGIN PRIVATE KEY-----\\nABC\\n-----END PRIVATE KEY-----\\n"
    }
    normalized = _normalize_firebase_credentials_payload(payload)
    assert "\n" in normalized["private_key"]
    assert "\\n" not in normalized["private_key"]


def test_load_credentials_reports_missing_file(monkeypatch):
    monkeypatch.setattr("app.services.firebase_push_service.settings.FIREBASE_CREDENTIALS_JSON", "")
    monkeypatch.setattr(
        "app.services.firebase_push_service.settings.FIREBASE_CREDENTIALS_PATH",
        "/missing/firebase.json",
    )
    payload, error = _load_firebase_credentials_payload()
    assert payload is None
    assert "not found" in (error or "").lower()


def test_send_push_reports_credentials_error(monkeypatch):
    monkeypatch.setattr("app.services.firebase_push_service.settings.FIREBASE_CREDENTIALS_JSON", "{bad json")
    monkeypatch.setattr("app.services.firebase_push_service.settings.FIREBASE_CREDENTIALS_PATH", "")
    result = send_push_to_tokens(["abc"], title="t", body="b")
    assert result.sent_count == 0
    assert result.credentials_error
    assert len(result.failures) == 1


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


def test_missing_credentials_file_reports_mount_hint(monkeypatch):
    from app.services import firebase_push_service as service
    error = "FIREBASE_CREDENTIALS_PATH file not found: /opt/app/secrets/ih-pro-firebase-adminsdk.json"
    monkeypatch.setattr(service, "_ensure_firebase", lambda: (False, None, error))
    result = service.send_push_to_tokens(["test-device"], title="New message", body="Test")
    assert result.sent_count == 0
    assert result.firebase_project_id is None
    failure = result.failures[0]
    assert failure["error_code"] == "CredentialsFileError"
    assert "Mount" in failure["hint"]
    assert "Regenerate" not in failure["hint"]
    assert "JSON loaded" not in failure["hint"]


def test_unreadable_credentials_file_is_reported(monkeypatch, tmp_path):
    from app.services import firebase_push_service as service
    path = tmp_path / "credentials.json"
    path.write_text("{}")
    monkeypatch.setattr(service.settings, "FIREBASE_CREDENTIALS_JSON", "")
    monkeypatch.setattr(service.settings, "FIREBASE_CREDENTIALS_PATH", str(path))
    def denied(*args, **kwargs):
        raise PermissionError("denied")
    monkeypatch.setattr(service.Path, "read_text", denied)
    payload, error = service._load_firebase_credentials_payload()
    assert payload is None
    assert "not readable (PermissionError)" in error
    assert "application user can read" in service._hint_for_fcm_error("FirebaseInitError", error)


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
