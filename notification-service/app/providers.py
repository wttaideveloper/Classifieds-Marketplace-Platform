"""The things that actually reach a person: push (Firebase Cloud Messaging) and email (SMTP).

Each provider is small and replaceable. With no credentials configured the service still runs and only logs what it would send, so a
new install, a test run or a developer machine never sends a real message by accident. `set_providers` swaps them (tests do this).
"""
import json
import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class Providers(Protocol):
    def push(self, tokens: list[str], title: str, body: str, data: dict) -> list[str]:
        """Send to these device tokens. Returns the tokens that are no longer valid (so they can be removed). Raises to be retried."""

    def email(self, to: str, subject: str, body: str) -> None:
        """Send one email. Raises to be retried."""


class LogProviders:
    """Only logs. Keeps what it was asked to send in `sent`, which is what the tests read."""

    def __init__(self):
        self.sent: list[dict] = []

    def push(self, tokens, title, body, data):
        self.sent.append({"channel": "push", "tokens": list(tokens), "title": title, "body": body, "data": data})
        logger.info("push (not sent, no credentials): %s -> %d device(s)", title, len(tokens))
        return []

    def email(self, to, subject, body):
        self.sent.append({"channel": "email", "to": to, "subject": subject, "body": body})
        logger.info("email (not sent, no SMTP): %s -> %s", subject, to)


class RealProviders:
    def __init__(self, settings: Settings):
        self.s = settings
        self._fcm = None

    def _firebase(self):
        if self._fcm is None:
            import firebase_admin
            from firebase_admin import credentials, messaging

            if not firebase_admin._apps:
                if self.s.FIREBASE_CREDENTIALS_JSON.strip():
                    cred = credentials.Certificate(json.loads(self.s.FIREBASE_CREDENTIALS_JSON))
                else:
                    cred = credentials.Certificate(self.s.FIREBASE_CREDENTIALS_PATH)
                firebase_admin.initialize_app(cred)
            self._fcm = messaging
        return self._fcm

    def push(self, tokens, title, body, data):
        if not (self.s.FIREBASE_CREDENTIALS_JSON.strip() or self.s.FIREBASE_CREDENTIALS_PATH.strip()):
            logger.info("push (not sent, Firebase not configured): %s", title)
            return []
        messaging = self._firebase()
        message = messaging.MulticastMessage(
            tokens=tokens, notification=messaging.Notification(title=title, body=body),
            data={str(k): str(v) for k, v in (data or {}).items()},
        )
        response = messaging.send_each_for_multicast(message)
        dead = []
        for token, result in zip(tokens, response.responses):
            if not result.success and result.exception is not None and type(result.exception).__name__ in ("UnregisteredError", "SenderIdMismatchError"):
                dead.append(token)
        if response.failure_count and response.failure_count == len(tokens) and len(dead) < len(tokens):
            raise RuntimeError("Firebase did not accept the message")
        return dead

    def email(self, to, subject, body):
        if not self.s.SMTP_HOST:
            logger.info("email (not sent, SMTP not configured): %s -> %s", subject, to)
            return
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.s.SMTP_FROM, to, subject
        msg.set_content(body or subject)
        with smtplib.SMTP(self.s.SMTP_HOST, self.s.SMTP_PORT, timeout=20) as smtp:
            if self.s.SMTP_STARTTLS:
                smtp.starttls()
            if self.s.SMTP_USER:
                smtp.login(self.s.SMTP_USER, self.s.SMTP_PASSWORD)
            smtp.send_message(msg)


_override: Providers | None = None


def set_providers(providers: Providers | None) -> None:
    global _override
    _override = providers


def get_providers() -> Providers:
    return _override or RealProviders(get_settings())
