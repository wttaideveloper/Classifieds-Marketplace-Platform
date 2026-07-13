import json
import logging
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

_firebase_initialized = False


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
        if settings.FIREBASE_CREDENTIALS_JSON.strip():
            cred = credentials.Certificate(json.loads(settings.FIREBASE_CREDENTIALS_JSON))
        else:
            cred_path = Path(settings.FIREBASE_CREDENTIALS_PATH)
            if not cred_path.is_file():
                logger.warning("Firebase credentials file not found: %s", cred_path)
                return False
            cred = credentials.Certificate(str(cred_path))
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
) -> int:
    """Send FCM push notifications. Returns count of successfully sent messages."""
    if not tokens or not _ensure_firebase():
        return 0

    from firebase_admin import messaging

    payload_data = {key: str(value) for key, value in (data or {}).items()}
    sent = 0
    for token in tokens:
        try:
            messaging.send(
                messaging.Message(
                    notification=messaging.Notification(title=title, body=body),
                    data=payload_data,
                    token=token,
                )
            )
            sent += 1
        except Exception:
            logger.warning("FCM delivery failed for device token prefix=%s", token[:12], exc_info=True)
    return sent
