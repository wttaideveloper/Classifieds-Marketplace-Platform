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


def _load_firebase_credentials_payload() -> dict | None:
    try:
        if settings.FIREBASE_CREDENTIALS_JSON.strip():
            return json.loads(settings.FIREBASE_CREDENTIALS_JSON)
        if settings.FIREBASE_CREDENTIALS_PATH.strip():
            cred_path = Path(settings.FIREBASE_CREDENTIALS_PATH)
            if cred_path.is_file():
                return json.loads(cred_path.read_text(encoding="utf-8"))
        return None
    except Exception:
        return None


def _normalize_firebase_credentials_payload(payload: dict) -> dict:
    """Fix common .env escaping issues for inline service account JSON."""
    normalized = dict(payload)
    private_key = normalized.get("private_key")
    if isinstance(private_key, str):
        if "\\n" in private_key:
            normalized["private_key"] = private_key.replace("\\n", "\n")
        normalized["private_key"] = normalized["private_key"].strip()
    return normalized


def _firebase_project_id_from_settings() -> str | None:
    payload = _load_firebase_credentials_payload()
    if not payload:
        return None
    project_id = payload.get("project_id")
    return str(project_id) if project_id else None


def get_firebase_diagnostics() -> dict:
    payload = _load_firebase_credentials_payload()
    diagnostics: dict = {
        "firebase_configured": settings.firebase_configured,
        "credentials_source": None,
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

    if settings.FIREBASE_CREDENTIALS_JSON.strip():
        diagnostics["credentials_source"] = "FIREBASE_CREDENTIALS_JSON"
    elif settings.FIREBASE_CREDENTIALS_PATH.strip():
        diagnostics["credentials_source"] = "FIREBASE_CREDENTIALS_PATH"

    if not payload:
        diagnostics["firebase_init_error"] = "Unable to parse Firebase credentials JSON."
        return diagnostics

    payload = _normalize_firebase_credentials_payload(payload)
    diagnostics["firebase_project_id"] = payload.get("project_id")
    diagnostics["client_email"] = payload.get("client_email")
    private_key = payload.get("private_key") or ""
    diagnostics["private_key_format_ok"] = private_key.startswith("-----BEGIN PRIVATE KEY-----")

    try:
        import firebase_admin
        from firebase_admin import credentials

        if not firebase_admin._apps:
            cred = credentials.Certificate(payload)
            firebase_admin.initialize_app(cred)
        global _firebase_initialized
        _firebase_initialized = True
        diagnostics["firebase_init_ok"] = True
    except Exception as exc:
        diagnostics["firebase_init_error"] = f"{type(exc).__name__}: {exc}"

    return diagnostics


def _hint_for_fcm_error(error_code: str, message: str) -> str:
    normalized = error_code.lower()
    message_lower = message.lower()

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


def _ensure_firebase() -> bool:
    global _firebase_initialized
    if _firebase_initialized:
        return True
    if not settings.firebase_configured:
        return False

    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError:
        logger.warning("firebase-admin is not installed; push notifications disabled")
        return False

    if firebase_admin._apps:
        _firebase_initialized = True
        return True

    try:
        payload = _load_firebase_credentials_payload()
        if not payload:
            logger.warning("Firebase credentials JSON could not be loaded")
            return False
        payload = _normalize_firebase_credentials_payload(payload)
        cred = credentials.Certificate(payload)
        firebase_admin.initialize_app(cred)
        _firebase_initialized = True
        return True
    except Exception:
        logger.exception("Failed to initialize Firebase Admin SDK")
        return False


def send_push_to_tokens(
    tokens: list[str],
    *,
    title: str,
    body: str,
    data: dict | None = None,
) -> PushSendResult:
    """Send FCM push notifications."""
    result = PushSendResult(firebase_project_id=_firebase_project_id_from_settings())
    if not tokens or not _ensure_firebase():
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
