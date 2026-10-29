"""Encryption of stored provider tokens (Fernet). A token is never logged, returned by an API or put in a notification."""
import base64
import hashlib
import hmac
import json
import time
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


@lru_cache(maxsize=4)
def _fernet(key_text: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key_text.encode()).digest()))


def _key() -> str:
    return settings.MEETING_TOKEN_ENCRYPTION_KEY.strip() or ("meetings:" + settings.SECRET_KEY)


def encrypt(value: str | None) -> str | None:
    if not value:
        return None
    return _fernet(_key()).encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet(_key()).decrypt(value.encode()).decode()
    except InvalidToken:
        return None                       # the key changed: the merchant has to reconnect


# ---------------------------------------------------------------- the OAuth `state` that comes back with the provider's redirect

STATE_LIFETIME_SECONDS = 600


def sign_state(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps({**payload, "exp": int(time.time()) + STATE_LIFETIME_SECONDS}, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(_key().encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def read_state(state: str | None) -> dict | None:
    """The payload, or None when the text was changed, is not ours or has expired."""
    try:
        body, signature = (state or "").rsplit(".", 1)
        expected = hmac.new(_key().encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return data if data.get("exp", 0) >= time.time() else None
    except Exception:
        return None
