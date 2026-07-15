import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

_firebase_initialized = False


@dataclass
class PushSendResult:
    sent_count: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)
    firebase_project_id: str | None = None
    credentials_error: str | None = None


def _credentials_source() -> str | None:
    if settings.FIREBASE_CREDENTIALS_JSON.strip():
        return "FIREBASE_CREDENTIALS_JSON"
    if settings.FIREBASE_CREDENTIALS_PATH.strip():
        return "FIREBASE_CREDENTIALS_PATH"
    return None


def _load_firebase_credentials_payload() -> tuple[dict | None, str | None]:
    """Load service account JSON. Returns (payload, error_message)."""
    if settings.FIREBASE_CREDENTIALS_JSON.strip():
        try:
            return json.loads(settings.FIREBASE_CREDENTIALS_JSON), None
        except json.JSONDecodeError as exc:
            return None, f"FIREBASE_CREDENTIALS_JSON is not valid JSON: {exc}"

    if settings.FIREBASE_CREDENTIALS_PATH.strip():
        cred_path = Path(settings.FIREBASE_CREDENTIALS_PATH)
        if not cred_path.is_file():
            return None, f"FIREBASE_CREDENTIALS_PATH file not found: {settings.FIREBASE_CREDENTIALS_PATH}"
        try:
            return json.loads(cred_path.read_text(encoding="utf-8")), None
        except json.JSONDecodeError as exc:
            return None, f"FIREBASE_CREDENTIALS_PATH file is not valid JSON: {exc}"

    return None, "Firebase credentials are not configured."


def _normalize_firebase_credentials_payload(payload: dict) -> dict:
    """Fix common .env escaping issues for inline service account JSON."""
    normalized = dict(payload)
    private_key = normalized.get("private_key")
    if isinstance(private_key, str):
        if "\\n" in private_key:
            normalized["private_key"] = private_key.replace("\\n", "\n")
        normalized["private_key"] = normalized["private_key"].strip()
    return normalized


def _hint_for_fcm_error(error_code: str, message: str) -> str:
    normalized = error_code.lower()
    message_lower = message.lower()

    if error_code == "CredentialsParseError":
        return (
            "Use FIREBASE_CREDENTIALS_PATH pointing to the downloaded service account file, "
            "or fix malformed FIREBASE_CREDENTIALS_JSON in .env."
        )
    if error_code == "FirebaseInitError":
        return (
            "Service account JSON loaded but Firebase Admin SDK failed to initialize. "
            "Regenerate the key in Firebase Console and verify private_key formatting."
        )
    if "senderidmismatch" in normalized or "sender id mismatch" in message_lower:
        return (
            "Mobile app Firebase project does not match backend service account. "
            "Confirm google-services.json project_id equals FIREBASE credentials project_id."
        )
    if "unregistered" in normalized or "not registered" in message_lower:
        return "FCM token is invalid or expired. Re-open the app, get a fresh token, and call POST /devices/register again."
    if "thirdpartyauth" in normalized or "authentication" in message_lower:
        return "Enable 'Firebase Cloud Messaging API' in Google Cloud Console for this Firebase project."
    if "invalidargument" in normalized:
        return "FCM rejected the message payload or token format. Verify the registered token is a current FCM token."
    if "not found" in message_lower or "404" in message_lower:
        return "Firebase Cloud Messaging API may be disabled for this Google Cloud project."
    return "Check server logs for the full FCM error and verify mobile/backend use the same Firebase project."


def get_firebase_diagnostics() -> dict:
    diagnostics: dict = {
        "firebase_configured": settings.firebase_configured,
        "credentials_source": _credentials_source(),
        "credentials_path": settings.FIREBASE_CREDENTIALS_PATH.strip() or None,
        "credentials_file_exists": None,
        "firebase_project_id": None,
        "client_email": None,
        "private_key_format_ok": False,
        "firebase_init_ok": False,
        "firebase_init_error": None,
        "expected_mobile_project_id": "ih-pro",
    }

    if not settings.firebase_configured:
        diagnostics["firebase_init_error"] = "FIREBASE_CREDENTIALS_PATH or FIREBASE_CREDENTIALS_JSON is not set."
        return diagnostics

    if diagnostics["credentials_path"]:
        diagnostics["credentials_file_exists"] = Path(diagnostics["credentials_path"]).is_file()

    payload, load_error = _load_firebase_credentials_payload()
    if load_error:
        diagnostics["firebase_init_error"] = load_error
        return diagnostics

    payload = _normalize_firebase_credentials_payload(payload)
    diagnostics["firebase_project_id"] = payload.get("project_id")
    diagnostics["client_email"] = payload.get("client_email")
    private_key = payload.get("private_key") or ""
    diagnostics["private_key_format_ok"] = private_key.startswith("-----BEGIN PRIVATE KEY-----")
    if not diagnostics["private_key_format_ok"]:
        diagnostics["firebase_init_error"] = "private_key is missing or malformed in service account JSON."

    try:
        import firebase_admin
        from firebase_admin import credentials

        if not firebase_admin._apps:
            cred = credentials.Certificate(payload)
            firebase_admin.initialize_app(cred)
        global _firebase_initialized
        _firebase_initialized = True
        diagnostics["firebase_init_ok"] = True
        diagnostics["firebase_init_error"] = None
    except Exception as exc:
        diagnostics["firebase_init_error"] = f"{type(exc).__name__}: {exc}"

    return diagnostics


def _ensure_firebase() -> tuple[bool, str | None, str | None]:
    """Returns (ready, project_id, error_message)."""
    global _firebase_initialized
    if _firebase_initialized:
        payload, _ = _load_firebase_credentials_payload()
        project_id = payload.get("project_id") if payload else None
        return True, str(project_id) if project_id else None, None

    if not settings.firebase_configured:
        return False, None, "Firebase credentials are not configured."

    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError:
        return False, None, "firebase-admin package is not installed on the server."

    if firebase_admin._apps:
        _firebase_initialized = True
        payload, _ = _load_firebase_credentials_payload()
        project_id = payload.get("project_id") if payload else None
        return True, str(project_id) if project_id else None, None

    payload, load_error = _load_firebase_credentials_payload()
    if load_error:
        logger.warning("Firebase credentials load failed: %s", load_error)
        return False, None, load_error

    payload = _normalize_firebase_credentials_payload(payload)
    project_id = str(payload.get("project_id")) if payload.get("project_id") else None

    try:
        cred = credentials.Certificate(payload)
        firebase_admin.initialize_app(cred)
        _firebase_initialized = True
        return True, project_id, None
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        logger.exception("Failed to initialize Firebase Admin SDK")
        return False, project_id, message


def send_push_to_tokens(
    tokens: list[str],
    *,
    title: str,
    body: str,
    data: dict | None = None,
) -> PushSendResult:
    """Send FCM push notifications."""
    ready, project_id, init_error = _ensure_firebase()
    result = PushSendResult(firebase_project_id=project_id, credentials_error=init_error)

    if not tokens:
        return result

    if not ready:
        result.failures.append(
            {
                "error_code": "CredentialsParseError" if init_error and "JSON" in init_error else "FirebaseInitError",
                "message": init_error or "Firebase Admin SDK is not initialized.",
                "hint": _hint_for_fcm_error(
                    "CredentialsParseError" if init_error and "JSON" in init_error else "FirebaseInitError",
                    init_error or "",
                ),
            }
        )
        return result

    from firebase_admin import messaging

    payload_data = {key: str(value) for key, value in (data or {}).items()}
    for token in tokens:
        try:
            messaging.send(
                messaging.Message(
                    notification=messaging.Notification(title=title, body=body),
                    data=payload_data,
                    token=token,
                    android=messaging.AndroidConfig(priority="high"),
                )
            )
            result.sent_count += 1
        except Exception as exc:
            error_code = type(exc).__name__
            message = str(exc)
            logger.warning(
                "FCM delivery failed for device token prefix=%s error=%s message=%s",
                token[:12],
                error_code,
                message,
                exc_info=True,
            )
            result.failures.append(
                {
                    "token_prefix": token[:12],
                    "error_code": error_code,
                    "message": message,
                    "hint": _hint_for_fcm_error(error_code, message),
                }
            )
    return result
