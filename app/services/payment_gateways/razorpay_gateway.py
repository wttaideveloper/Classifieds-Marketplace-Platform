"""Razorpay adapter (REST API v1).

Flow: create an Order here -> the customer pays in Razorpay Checkout in the browser / app -> the client sends back
razorpay_order_id, razorpay_payment_id and razorpay_signature -> `confirm` checks the signature and reads the payment.
Webhooks are checked with X-Razorpay-Signature.

NOTE: written from Razorpay's public API documentation and covered by tests of its signature checks and of how it reads
responses. It has not been run against a live Razorpay account: try it with test keys (rzp_test_...) before going live.
"""
import hashlib
import hmac
import json
from typing import Any

import requests

from app.core.config import settings
from app.services.payment_gateways.base import (
    AUTHORIZED, CANCELLED, FAILED, REQUIRES_PAYMENT_METHOD, SUCCEEDED, GatewayCustomer, GatewayError, GatewayRequest, GatewayResult,
    GatewayTimeout, MethodInfo, PaymentGateway, RefundResult, WebhookEvent, WebhookSignatureError,
)

BASE_URL = "https://api.razorpay.com/v1"
PAYMENT_STATUS = {"created": REQUIRES_PAYMENT_METHOD, "authorized": AUTHORIZED, "captured": SUCCEEDED, "refunded": SUCCEEDED, "failed": FAILED}


def verify_payment_signature(order_id: str, payment_id: str, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


class RazorpayGateway(PaymentGateway):
    name = "razorpay"

    def __init__(self, key_id: str | None = None, key_secret: str | None = None, webhook_secret: str | None = None, session=None):
        self.key_id = key_id if key_id is not None else settings.RAZORPAY_KEY_ID
        self.key_secret = key_secret if key_secret is not None else settings.RAZORPAY_KEY_SECRET
        self.webhook_secret = webhook_secret if webhook_secret is not None else settings.RAZORPAY_WEBHOOK_SECRET
        self.session = session or requests

    # --- plumbing
    def _call(self, method: str, path: str, payload: dict | None = None, idempotency_key: str | None = None) -> dict:
        if not self.key_id or not self.key_secret:
            raise GatewayError("Razorpay is not configured (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET)", code="not_configured")
        headers = {"X-Razorpay-Idempotency": idempotency_key} if idempotency_key else {}
        try:
            response = self.session.request(method, f"{BASE_URL}{path}", json=payload if method != "GET" else None, params=payload if method == "GET" else None,
                                            auth=(self.key_id, self.key_secret), headers=headers, timeout=settings.PAYMENT_GATEWAY_TIMEOUT_SECONDS)
        except requests.Timeout as exc:
            raise GatewayTimeout() from exc
        except requests.RequestException as exc:
            raise GatewayError(f"Could not reach Razorpay: {type(exc).__name__}", code="network_error", retryable=True) from exc
        if response.status_code >= 500:
            raise GatewayError("Razorpay had a problem. Try again shortly", code="gateway_unavailable", retryable=True)
        body = response.json() if response.content else {}
        if response.status_code >= 400:
            error = body.get("error") or {}
            raise GatewayError(error.get("description") or "Razorpay refused the request", code=error.get("code") or "gateway_rejected")
        return body

    @staticmethod
    def _from_payment(order_id: str | None, payment: dict) -> GatewayResult:
        status = PAYMENT_STATUS.get(payment.get("status"), REQUIRES_PAYMENT_METHOD)
        card = payment.get("card") or {}
        return GatewayResult(
            status=status, gateway_order_id=order_id or payment.get("order_id"), gateway_payment_id=payment.get("id"),
            method_brand=(card.get("network") or payment.get("method") or "").lower() or None, method_last4=card.get("last4"),
            fee_minor=payment.get("fee") if status == SUCCEEDED else None, failure_code=payment.get("error_code") if status == FAILED else None,
            failure_message=payment.get("error_description") if status == FAILED else None, raw={"id": payment.get("id"), "status": payment.get("status")},
        )

    # --- interface
    def create(self, request: GatewayRequest) -> GatewayResult:
        order = self._call("POST", "/orders", {
            "amount": request.amount_minor, "currency": request.currency, "receipt": request.payment_id[:40],
            "payment_capture": 1 if request.capture_method == "automatic" else 0,
            "notes": {"payment_id": request.payment_id, **{k: str(v)[:250] for k, v in request.metadata.items()}},
        }, request.idempotency_key)
        params = {"gateway": "razorpay", "key_id": self.key_id, "order_id": order["id"], "amount": order["amount"], "currency": order["currency"],
                  "name": request.customer.name, "email": request.customer.email}
        if request.saved_token:
            params.update({"customer_id": request.customer.gateway_customer_id, "token": request.saved_token, "recurring": False})
        return GatewayResult(status=REQUIRES_PAYMENT_METHOD, gateway_order_id=order["id"], client_params=params, raw={"id": order["id"]})

    def confirm(self, request: GatewayRequest, client_data: dict[str, Any]) -> GatewayResult:
        order_id = client_data.get("razorpay_order_id") or request.gateway_order_id
        payment_id = client_data.get("razorpay_payment_id")
        if order_id != request.gateway_order_id:
            raise GatewayError("That payment belongs to another order", code="order_mismatch")
        if not payment_id:
            return self.fetch(request)
        if not verify_payment_signature(order_id, payment_id, client_data.get("razorpay_signature", ""), self.key_secret):
            raise GatewayError("The payment signature is wrong", code="signature_mismatch")
        payment = self._call("GET", f"/payments/{payment_id}")
        if payment.get("order_id") != order_id:
            raise GatewayError("That payment belongs to another order", code="order_mismatch")
        return self._from_payment(order_id, payment)

    def fetch(self, request: GatewayRequest) -> GatewayResult:
        body = self._call("GET", f"/orders/{request.gateway_order_id}/payments")
        items = body.get("items") or []
        if not items:
            return GatewayResult(status=REQUIRES_PAYMENT_METHOD, gateway_order_id=request.gateway_order_id)
        rank = {SUCCEEDED: 3, AUTHORIZED: 2, FAILED: 0}
        best = max(items, key=lambda p: rank.get(PAYMENT_STATUS.get(p.get("status"), ""), 1))
        return self._from_payment(request.gateway_order_id, best)

    def capture(self, request: GatewayRequest) -> GatewayResult:
        payment = self._call("POST", f"/payments/{request.gateway_payment_id}/capture", {"amount": request.amount_minor, "currency": request.currency})
        return self._from_payment(request.gateway_order_id, payment)

    def cancel(self, request: GatewayRequest) -> GatewayResult:
        """Razorpay cannot cancel an authorized payment: a full refund releases the hold. An unpaid order just expires."""
        if request.gateway_payment_id:
            self._call("POST", f"/payments/{request.gateway_payment_id}/refund", {"amount": request.amount_minor}, request.idempotency_key)
        return GatewayResult(status=CANCELLED, gateway_order_id=request.gateway_order_id, gateway_payment_id=request.gateway_payment_id)

    def refund(self, request: GatewayRequest, amount_minor: int, reason: str | None, idempotency_key: str) -> RefundResult:
        try:
            body = self._call("POST", f"/payments/{request.gateway_payment_id}/refund",
                              {"amount": amount_minor, "speed": "normal", "notes": {"reason": (reason or "")[:250]}, "receipt": idempotency_key[:40]}, idempotency_key)
        except GatewayError as exc:
            if exc.retryable:
                raise
            return RefundResult(status="failed", failure_message=exc.message)
        return RefundResult(status="succeeded" if body.get("status") == "processed" else "pending", gateway_refund_id=body.get("id"), raw={"status": body.get("status")})

    def parse_webhook(self, headers: dict[str, str], raw_body: bytes) -> list[WebhookEvent]:
        lowered = {k.lower(): v for k, v in headers.items()}
        expected = hmac.new((self.webhook_secret or "").encode(), raw_body, hashlib.sha256).hexdigest()
        if not self.webhook_secret or not hmac.compare_digest(lowered.get("x-razorpay-signature", ""), expected):
            raise WebhookSignatureError("The webhook signature is wrong")
        body = json.loads(raw_body or b"{}")
        event = str(body.get("event", ""))
        entity = ((body.get("payload") or {}).get("payment") or {}).get("entity") or {}
        refund = ((body.get("payload") or {}).get("refund") or {}).get("entity") or {}
        event_id = lowered.get("x-razorpay-event-id") or f"{event}:{entity.get('id') or refund.get('id')}"
        mapping = {"payment.captured": "payment.succeeded", "order.paid": "payment.succeeded", "payment.authorized": "payment.authorized",
                   "payment.failed": "payment.failed", "refund.processed": "refund.succeeded", "refund.failed": "refund.failed"}
        if event not in mapping:
            return []
        card = entity.get("card") or {}
        return [WebhookEvent(
            event_id=event_id, type=mapping[event], gateway_order_id=entity.get("order_id"), gateway_payment_id=entity.get("id") or refund.get("payment_id"),
            gateway_refund_id=refund.get("id"), amount_minor=entity.get("amount") or refund.get("amount"), currency=entity.get("currency") or refund.get("currency"),
            fee_minor=entity.get("fee"), method_brand=(card.get("network") or "").lower() or None, method_last4=card.get("last4"),
            failure_code=entity.get("error_code"), failure_message=entity.get("error_description"), raw=body,
        )]

    def save_method(self, customer: GatewayCustomer, client_data: dict[str, Any]) -> MethodInfo:
        token = client_data.get("token")
        if not token:
            raise GatewayError("Send the token Razorpay Checkout returned", code="invalid_token")
        customer_id = customer.gateway_customer_id
        if not customer_id:
            made = self._call("POST", "/customers", {"name": customer.name or "Customer", "email": customer.email, "fail_existing": "0", "notes": {"user_id": customer.user_id}})
            customer_id = made["id"]
        details = self._call("GET", f"/customers/{customer_id}/tokens/{token}")
        card = details.get("card") or {}
        return MethodInfo(token=token, brand=(card.get("network") or "").lower() or None, last4=card.get("last4"),
                          exp_month=int(card["expiry_month"]) if card.get("expiry_month") else None,
                          exp_year=int(card["expiry_year"]) if card.get("expiry_year") else None, gateway_customer_id=customer_id)

    def remove_method(self, token: str, gateway_customer_id: str | None) -> None:
        if gateway_customer_id:
            try:
                self._call("DELETE", f"/customers/{gateway_customer_id}/tokens/{token}")
            except GatewayError:
                pass
