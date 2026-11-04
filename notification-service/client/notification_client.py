"""A tiny client for the services that send events. Copy this one file into any module or app (needs only httpx).

    from notification_client import NotificationClient
    notify = NotificationClient("https://notifications.internal", api_key="payments-key")
    notify.send("payment.succeeded", ["user-id"], data={"amount": "1180.00", "currency": "INR"}, idempotency_key=f"payment-{payment_id}:succeeded")

A failure is logged and swallowed by default: a notification must never break the business action that caused it.
"""
import logging

import httpx

logger = logging.getLogger(__name__)


class NotificationClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 3.0, raise_errors: bool = False):
        self.base_url, self.timeout, self.raise_errors = base_url.rstrip("/"), timeout, raise_errors
        self.headers = {"X-API-Key": api_key}

    def _call(self, method: str, path: str, **kwargs):
        try:
            response = httpx.request(method, self.base_url + path, headers=self.headers, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            return response.json()
        except Exception:
            logger.exception("notification service call failed: %s %s", method, path)
            if self.raise_errors:
                raise
            return None

    def send(self, event: str, user_ids: list[str], *, data: dict | None = None, emails: dict[str, str] | None = None, tenant_id: str | None = None,
             idempotency_key: str | None = None, channels: list[str] | None = None):
        recipients = [{"user_id": u, **({"email": emails[u]} if emails and u in emails else {})} for u in user_ids]
        body = {"event": event, "recipients": recipients, "data": data or {}, "tenant_id": tenant_id, "idempotency_key": idempotency_key, "channels": channels}
        return self._call("POST", "/v1/events", json={k: v for k, v in body.items() if v is not None})

    def inbox(self, user_id: str, *, unread_only: bool = False, page: int = 1, page_size: int = 20):
        return self._call("GET", f"/v1/users/{user_id}/notifications", params={"unread_only": unread_only, "page": page, "page_size": page_size})

    def mark_read(self, user_id: str, ids: list[str] | None = None, all: bool = False):
        return self._call("POST", f"/v1/users/{user_id}/notifications/read", json={"ids": ids, "all": all})

    def register_device(self, user_id: str, token: str, platform: str = "android"):
        return self._call("POST", f"/v1/users/{user_id}/devices", json={"token": token, "platform": platform})
