"""A gateway that moves no money, for development, QA and the automated tests.

The customer side is simulated with test tokens, the same idea as Stripe's test cards:

    tok_success       the payment succeeds (visa 4242)
    tok_fail          declined
    tok_insufficient  declined: insufficient funds
    tok_3ds           needs 3-D Secure; confirm again with {"authenticated": true} (or false to fail it)
    tok_timeout       the gateway does not answer (the payment must then be checked)
    tok_risky         succeeds, but is a card the risk check should look at

It remembers the payments it created in memory so `fetch` can answer. Webhooks are signed with TEST_GATEWAY_WEBHOOK_SECRET
(HMAC-SHA256 of the body, in the header X-Test-Signature).
"""
import hashlib
import hmac
import json
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.services.payment_gateways.base import (
    AUTHORIZED, CANCELLED, FAILED, REQUIRES_ACTION, REQUIRES_PAYMENT_METHOD, SUCCEEDED, GatewayError, GatewayRequest,
    GatewayResult, GatewayTimeout, MethodInfo, PaymentGateway, RefundResult, WebhookEvent, WebhookSignatureError,
)

# gateway_order_id -> {"status", "gateway_payment_id", "amount_minor", "currency", "refunded_minor", "token"}
ORDERS: dict[str, dict] = {}
FEE_PERCENT = 2


class TestGateway(PaymentGateway):
    __test__ = False   # not a pytest class
    name = "test"
    fail_next_refund: str | None = None      # set to a message to make the next refund fail (tests)

    def create(self, request: GatewayRequest) -> GatewayResult:
        order_id = f"test_order_{uuid4().hex[:16]}"
        ORDERS[order_id] = {"status": REQUIRES_PAYMENT_METHOD, "gateway_payment_id": None, "amount_minor": request.amount_minor,
                            "currency": request.currency, "refunded_minor": 0, "token": None, "capture_method": request.capture_method}
        result = GatewayResult(
            status=REQUIRES_PAYMENT_METHOD, gateway_order_id=order_id,
            client_params={"gateway": "test", "order_id": order_id, "amount": request.amount_minor, "currency": request.currency,
                           "test_tokens": ["tok_success", "tok_fail", "tok_insufficient", "tok_3ds", "tok_timeout", "tok_risky"]},
        )
        if request.saved_token:
            request.gateway_order_id = order_id
            return self._pay(request, request.saved_token, authenticated=None)
        return result

    def confirm(self, request: GatewayRequest, client_data: dict[str, Any]) -> GatewayResult:
        token = client_data.get("test_token") or client_data.get("token")
        order = ORDERS.get(request.gateway_order_id or "")
        if order is None:
            raise GatewayError("The gateway does not know this payment", code="order_not_found")
        if token is None and order.get("token"):
            token = order["token"]
        if token is None:
            return self._result(request.gateway_order_id)
        return self._pay(request, token, authenticated=client_data.get("authenticated"))

    def _pay(self, request: GatewayRequest, token: str, authenticated) -> GatewayResult:
        order_id = request.gateway_order_id
        order = ORDERS[order_id]
        order["token"] = token
        if token == "tok_timeout":
            raise GatewayTimeout()
        if token == "tok_3ds" and authenticated is None:
            order["status"] = REQUIRES_ACTION
            order["gateway_payment_id"] = order["gateway_payment_id"] or f"test_pay_{uuid4().hex[:16]}"
            result = self._result(order_id)
            result.next_action = {"type": "redirect", "url": f"https://test-gateway.invalid/3ds/{order_id}", "method": "3ds"}
            return result
        if token == "tok_3ds" and authenticated is False:
            return self._fail(order_id, "authentication_failed", "The customer did not pass 3-D Secure")
        if token == "tok_fail":
            return self._fail(order_id, "card_declined", "The card was declined")
        if token == "tok_insufficient":
            return self._fail(order_id, "insufficient_funds", "The card has insufficient funds")
        if token in ("tok_success", "tok_3ds", "tok_risky") or token.startswith("tok_saved_"):
            order["gateway_payment_id"] = order["gateway_payment_id"] or f"test_pay_{uuid4().hex[:16]}"
            order["status"] = AUTHORIZED if order["capture_method"] == "manual" else SUCCEEDED
            return self._result(order_id)
        return self._fail(order_id, "invalid_token", "That test token is not recognised")

    def _fail(self, order_id: str, code: str, message: str) -> GatewayResult:
        order = ORDERS[order_id]
        order["status"] = FAILED
        order["failure"] = (code, message)
        return self._result(order_id)

    def _result(self, order_id: str | None) -> GatewayResult:
        order = ORDERS.get(order_id or "")
        if order is None:
            raise GatewayError("The gateway does not know this payment", code="order_not_found")
        failure = order.get("failure") or (None, None)
        paid = order["status"] in (SUCCEEDED, AUTHORIZED)
        return GatewayResult(
            status=order["status"], gateway_order_id=order_id, gateway_payment_id=order["gateway_payment_id"],
            method_brand="visa" if paid else None, method_last4="4242" if paid else None,
            fee_minor=(order["amount_minor"] * FEE_PERCENT // 100) if order["status"] == SUCCEEDED else None,
            failure_code=failure[0] if order["status"] == FAILED else None, failure_message=failure[1] if order["status"] == FAILED else None,
            raw={"test": True},
        )

    def fetch(self, request: GatewayRequest) -> GatewayResult:
        if request.gateway_order_id not in ORDERS:
            return GatewayResult(status=FAILED, gateway_order_id=request.gateway_order_id, failure_code="not_found",
                                 failure_message="The gateway has no record of this payment")
        return self._result(request.gateway_order_id)

    def capture(self, request: GatewayRequest) -> GatewayResult:
        order = ORDERS.get(request.gateway_order_id or "")
        if order is None or order["status"] != AUTHORIZED:
            raise GatewayError("Only an authorized payment can be captured", code="not_capturable")
        order["status"] = SUCCEEDED
        return self._result(request.gateway_order_id)

    def cancel(self, request: GatewayRequest) -> GatewayResult:
        order = ORDERS.get(request.gateway_order_id or "")
        if order is None:
            raise GatewayError("The gateway does not know this payment", code="order_not_found")
        if order["status"] == SUCCEEDED:
            raise GatewayError("A captured payment cannot be cancelled. Refund it", code="already_captured")
        order["status"] = CANCELLED
        return self._result(request.gateway_order_id)

    def refund(self, request: GatewayRequest, amount_minor: int, reason: str | None, idempotency_key: str) -> RefundResult:
        if TestGateway.fail_next_refund:
            message, TestGateway.fail_next_refund = TestGateway.fail_next_refund, None
            return RefundResult(status="failed", failure_message=message)
        order = ORDERS.get(request.gateway_order_id or "")
        if order is None or order["status"] != SUCCEEDED:
            return RefundResult(status="failed", failure_message="Only a captured payment can be refunded")
        if order["refunded_minor"] + amount_minor > order["amount_minor"]:
            return RefundResult(status="failed", failure_message="The refund is more than what is left of the payment")
        order["refunded_minor"] += amount_minor
        digest = hashlib.sha1(f"{idempotency_key}".encode()).hexdigest()[:16]
        return RefundResult(status="succeeded", gateway_refund_id=f"test_refund_{digest}", raw={"test": True})

    def parse_webhook(self, headers: dict[str, str], raw_body: bytes) -> list[WebhookEvent]:
        signature = {k.lower(): v for k, v in headers.items()}.get("x-test-signature", "")
        expected = hmac.new(settings.TEST_GATEWAY_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise WebhookSignatureError("The webhook signature is wrong")
        body = json.loads(raw_body or b"{}")
        data = body.get("data") or {}
        return [WebhookEvent(
            event_id=str(body.get("id")), type=str(body.get("type")), gateway_order_id=data.get("order_id"),
            gateway_payment_id=data.get("payment_id"), gateway_refund_id=data.get("refund_id"), amount_minor=data.get("amount"),
            currency=data.get("currency"), fee_minor=data.get("fee"), failure_code=data.get("failure_code"),
            failure_message=data.get("failure_message"), raw=body,
        )]

    def save_method(self, customer, client_data: dict[str, Any]) -> MethodInfo:
        token = str(client_data.get("token") or "")
        if not token.startswith("tok_saved_"):
            raise GatewayError("Test saved methods are tokens that start with tok_saved_, for example tok_saved_visa_4242", code="invalid_token")
        parts = token.split("_")
        return MethodInfo(token=token, brand=parts[2] if len(parts) > 2 else "visa", last4=parts[3] if len(parts) > 3 else "4242",
                          exp_month=int(client_data.get("exp_month") or 12), exp_year=int(client_data.get("exp_year") or 2030),
                          gateway_customer_id=f"test_cus_{customer.user_id}")


def sign(body: bytes) -> str:
    """The signature the test gateway would send for this body (used by the tests and by QA tooling)."""
    return hmac.new(settings.TEST_GATEWAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
