import logging
import re

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

_E164_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_phone_number(phone: str) -> str | None:
    """Normalize to E.164-ish format (+country + number)."""
    cleaned = re.sub(r"[^\d+]", "", phone.strip())
    if not cleaned:
        return None
    if cleaned.startswith("+"):
        normalized = cleaned
    elif len(cleaned) == 10:
        normalized = f"+1{cleaned}"
    else:
        normalized = f"+{cleaned.lstrip('+')}"
    if not _E164_PATTERN.match(normalized):
        return None
    return normalized


def send_sms(*, phone: str, message: str, reference_id: str | None = None) -> bool:
    """Send SMS via Bravo. Returns True when the provider accepts the message."""
    if not settings.bravo_sms_configured:
        logger.debug("Bravo SMS not configured; skipping SMS send")
        return False

    normalized = normalize_phone_number(phone)
    if not normalized:
        logger.warning("Invalid SMS phone number for Bravo delivery")
        return False

    text = message.strip()
    if len(text) > 1600:
        text = text[:1597] + "..."

    payload = {
        "to": normalized,
        "message": text,
    }
    if reference_id:
        payload["reference_id"] = reference_id

    headers = {
        "Authorization": f"Bearer {settings.BRAVO_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            settings.BRAVO_SMS_API_URL,
            json=payload,
            headers=headers,
            timeout=settings.BRAVO_SMS_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return True
    except requests.RequestException:
        logger.exception("Bravo SMS delivery failed for phone prefix=%s", normalized[:6])
        return False
