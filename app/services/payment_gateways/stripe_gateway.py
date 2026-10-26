"""Stripe adapter (REST API, PaymentIntents).

Flow: create a PaymentIntent here -> the browser / app confirms it with Stripe.js / the mobile SDK using the client_secret
(3-D Secure is handled there) -> `confirm` reads the PaymentIntent from Stripe and returns its true state. Webhooks are
checked with the Stripe-Signature header (HMAC-SHA256 over "timestamp.body", five-minute tolerance).

NOTE: written from Stripe's public API documentation and covered by tests of its signature check and of how it reads
responses. It has not been run against a live Stripe account: try it with test keys (sk_test_...) before going live.
"""
import hashlib
import hmac
import json
import time
from typing import Any

import requests

from app.core.config import settings
from app.services.payment_gateways.base import (
    AUTHORIZED, CANCELLED, FAILED, PROCESSING, REQUIRES_ACTION, REQUIRES_PAYMENT_METHOD, SUCCEEDED, GatewayCustomer, GatewayError,
    GatewayRequest, GatewayResult, GatewayTimeout, MethodInfo, PaymentGateway, RefundResult, WebhookEvent, WebhookSignatureError,
)

BASE_URL = "https://api.stripe.com/v1"
INTENT_STATUS = {
    "requires_payment_method": REQUIRES_PAYMENT_METHOD, "requires_confirmation": REQUIRES_PAYMENT_METHOD, "requires_action": REQUIRES_ACTION,
    "processing": PROCESSING, "requires_capture": AUTHORIZED, "succeeded": SUCCEEDED, "canceled": CANCELLED,
}
SIGNATURE_TOLERANCE_SECONDS = 300


def _flatten(prefix: str, value: Any, out: dict) -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            _flatten(f"{prefix}[{key}]" if prefix else key, inner, out)
    elif value is not None:
        out[prefix] = str(value).lower() if isinstance(value, bool) else value


def form(payload: dict) -> dict:
    """Stripe takes nested fields as metadata[key]=value."""
    out: dict = {}
    _flatten("", payload, out)
    return out


def verify_signature(header: str, raw_body: bytes, secret: str, now: float | None = None) -> bool:
    """Stripe may send several `v1=` signatures at once (while a signing secret is being rolled), so any one may match."""
    pairs = [p.split("=", 1) for p in (header or "").split(",") if "=" in p]
    timestamps = [value for key, value in pairs if key.strip() == "t"]
    signatures = [value for key, value in pairs if key.strip() == "v1"]
    if not timestamps or not signatures or not secret:
        return False
    try:
        timestamp = int(timestamps[0])
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, signature) for signature in signatures)


class StripeGateway(PaymentGateway):
    name = "stripe"

    def __init__(self, secret_key: str | None = None, webhook_secret: str | None = None, session=None):
        self.secret_key = secret_key if secret_key is not None else settings.STRIPE_SECRET_KEY
        self.webhook_secret = webhook_secret if webhook_secret is not None else settings.STRIPE_WEBHOOK_SECRET
        self.session = session or requests

    def _call(self, method: str, path: str, payload: dict | None = None, idempotency_key: str | None = None) -> dict:
        if not self.secret_key:
            raise GatewayError("Stripe is not configured (STRIPE_SECRET_KEY)", code="not_configured")
        headers = {"Authorization": f"Bearer {self.secret_key}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            response = self.session.request(method, f"{BASE_URL}{path}", data=form(payload or {}) if method != "GET" else None,
                                            params=form(payload or {}) if method == "GET" else None, headers=headers,
                                            timeout=settings.PAYMENT_GATEWAY_TIMEOUT_SECONDS)
        except requests.Timeout as exc:
            raise GatewayTimeout() from exc
        except requests.RequestException as exc:
            raise GatewayError(f"Could not reach Stripe: {type(exc).__name__}", code="network_error", retryable=True) from exc
        if response.status_code >= 500:
            raise GatewayError("Stripe had a problem. Try again shortly", code="gateway_unavailable", retryable=True)
        body = response.json() if response.content else {}
        if response.status_code >= 400:
            error = body.get("error") or {}
            raise GatewayError(error.get("message") or "Stripe refused the request", code=error.get("code") or error.get("type") or "gateway_rejected")
        return body

    @staticmethod
    def _from_intent(intent: dict) -> GatewayResult:
        status = INTENT_STATUS.get(intent.get("status"), REQUIRES_PAYMENT_METHOD)
        charge = intent.get("latest_charge") if isinstance(intent.get("latest_charge"), dict) else {}
        card = ((charge.get("payment_method_details") or {}).get("card")) or {}
        balance = charge.get("balance_transaction") if isinstance(charge.get("balance_transaction"), dict) else {}
        error = intent.get("last_payment_error") or {}
        action = intent.get("next_action") or {}
        next_action = None
        if status == REQUIRES_ACTION:
            redirect = (action.get("redirect_to_url") or {}).get("url")
            next_action = {"type": "redirect" if redirect else "sdk", "url": redirect, "client_secret": intent.get("client_secret")}
        return GatewayResult(
            status=FAILED if (status == REQUIRES_PAYMENT_METHOD and error) else status, gateway_order_id=intent.get("id"),
            gateway_payment_id=charge.get("id") if charge else intent.get("id"), next_action=next_action,
            method_brand=card.get("brand"), method_last4=card.get("last4"), fee_minor=balance.get("fee") if balance else None,
            failure_code=error.get("code") or error.get("decline_code"), failure_message=error.get("message"),
            raw={"id": intent.get("id"), "status": intent.get("status")},
        )

    def create(self, request: GatewayRequest) -> GatewayResult:
        payload = {
            "amount": request.amount_minor, "currency": request.currency.lower(), "capture_method": request.capture_method,
            "description": request.description or None, "receipt_email": request.customer.email,
            "metadata": {"payment_id": request.payment_id, **{k: str(v)[:450] for k, v in request.metadata.items()}},
        }
        if request.saved_token and request.customer.gateway_customer_id:
            payload.update({"customer": request.customer.gateway_customer_id, "payment_method": request.saved_token, "confirm": True,
                            "return_url": request.return_url or settings.FRONTEND_URL})
        else:
            payload["automatic_payment_methods"] = {"enabled": True}
        intent = self._call("POST", "/payment_intents", payload, request.idempotency_key)
        result = self._from_intent(intent)
        result.client_params = {"gateway": "stripe", "client_secret": intent.get("client_secret"), "payment_intent_id": intent["id"]}
        return result

    def confirm(self, request: GatewayRequest, client_data: dict[str, Any]) -> GatewayResult:
        intent_id = client_data.get("payment_intent_id") or request.gateway_order_id
        if intent_id != request.gateway_order_id:
            raise GatewayError("That payment belongs to another order", code="order_mismatch")
        return self.fetch(request)

    def fetch(self, request: GatewayRequest) -> GatewayResult:
        intent = self._call("GET", f"/payment_intents/{request.gateway_order_id}", {"expand": ["latest_charge.balance_transaction"]})
        return self._from_intent(intent)

    def capture(self, request: GatewayRequest) -> GatewayResult:
        intent = self._call("POST", f"/payment_intents/{request.gateway_order_id}/capture", {"amount_to_capture": request.amount_minor}, request.idempotency_key)
        return self._from_intent(intent)

    def cancel(self, request: GatewayRequest) -> GatewayResult:
        intent = self._call("POST", f"/payment_intents/{request.gateway_order_id}/cancel", {}, request.idempotency_key)
        return self._from_intent(intent)

    def refund(self, request: GatewayRequest, amount_minor: int, reason: str | None, idempotency_key: str) -> RefundResult:
        try:
            body = self._call("POST", "/refunds", {"payment_intent": request.gateway_order_id, "amount": amount_minor,
                                                    "metadata": {"reason": (reason or "")[:450]}}, idempotency_key)
        except GatewayError as exc:
            if exc.retryable:
                raise
            return RefundResult(status="failed", failure_message=exc.message)
        status = {"succeeded": "succeeded", "pending": "pending", "requires_action": "pending", "failed": "failed", "canceled": "failed"}.get(body.get("status"), "pending")
        return RefundResult(status=status, gateway_refund_id=body.get("id"), failure_message=body.get("failure_reason"), raw={"status": body.get("status")})

    def parse_webhook(self, headers: dict[str, str], raw_body: bytes) -> list[WebhookEvent]:
        lowered = {k.lower(): v for k, v in headers.items()}
        if not verify_signature(lowered.get("stripe-signature", ""), raw_body, self.webhook_secret):
            raise WebhookSignatureError("The webhook signature is wrong")
        body = json.loads(raw_body or b"{}")
        kind, obj = str(body.get("type", "")), (body.get("data") or {}).get("object") or {}
        base = {"event_id": str(body.get("id")), "raw": body}
        if kind.startswith("payment_intent."):
            mapping = {"payment_intent.succeeded": "payment.succeeded", "payment_intent.amount_capturable_updated": "payment.authorized",
                       "payment_intent.payment_failed": "payment.failed", "payment_intent.canceled": "payment.cancelled"}
            if kind not in mapping:
                return []
            error = obj.get("last_payment_error") or {}
            return [WebhookEvent(type=mapping[kind], gateway_order_id=obj.get("id"), gateway_payment_id=obj.get("latest_charge") if isinstance(obj.get("latest_charge"), str) else obj.get("id"),
                                 amount_minor=obj.get("amount"), currency=(obj.get("currency") or "").upper() or None,
                                 failure_code=error.get("code"), failure_message=error.get("message"), **base)]
        if kind in ("refund.updated", "charge.refund.updated", "refund.created"):
            status = obj.get("status")
            if status not in ("succeeded", "failed", "canceled"):
                return []
            return [WebhookEvent(type="refund.succeeded" if status == "succeeded" else "refund.failed", gateway_order_id=obj.get("payment_intent"),
                                 gateway_refund_id=obj.get("id"), amount_minor=obj.get("amount"), currency=(obj.get("currency") or "").upper() or None,
                                 failure_message=obj.get("failure_reason"), **base)]
        return []

    def save_method(self, customer: GatewayCustomer, client_data: dict[str, Any]) -> MethodInfo:
        token = client_data.get("token")
        if not token:
            raise GatewayError("Send the payment method id Stripe.js returned (pm_...)", code="invalid_token")
        customer_id = customer.gateway_customer_id
        if not customer_id:
            made = self._call("POST", "/customers", {"email": customer.email, "name": customer.name, "metadata": {"user_id": customer.user_id}})
            customer_id = made["id"]
        method = self._call("POST", f"/payment_methods/{token}/attach", {"customer": customer_id})
        card = method.get("card") or {}
        return MethodInfo(token=token, brand=card.get("brand"), last4=card.get("last4"), exp_month=card.get("exp_month"), exp_year=card.get("exp_year"),
                          gateway_customer_id=customer_id)

    def remove_method(self, token: str, gateway_customer_id: str | None) -> None:
        try:
            self._call("POST", f"/payment_methods/{token}/detach", {})
        except GatewayError:
            pass
