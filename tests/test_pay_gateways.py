"""The Razorpay and Stripe adapters: signature checks, how they build requests and read responses (with a fake HTTP session), and the
test gateway. The adapters have not been run against live accounts; these tests pin down the parts that can be checked offline."""
import hashlib
import hmac
import json
import time

import pytest
import requests

from app.services.payment_gateways import base as gw
from app.services.payment_gateways import get_gateway
from app.services.payment_gateways.razorpay_gateway import RazorpayGateway, verify_payment_signature
from app.services.payment_gateways.stripe_gateway import StripeGateway, form, verify_signature
from app.services.payment_gateways.test_gateway import TestGateway


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body if body is not None else {}
        self.content = json.dumps(self._body).encode()

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def request(**extra):
    base = dict(payment_id="11111111-2222-3333-4444-555555555555", amount_minor=118000, currency="INR", gateway_order_id="order_1", gateway_payment_id="pay_1")
    base.update(extra)
    return gw.GatewayRequest(**base)


# --- Razorpay -------------------------------------------------------------------------------------------------------------------------------

def razorpay(*responses):
    return RazorpayGateway(key_id="rzp_test_key", key_secret="s3cret", webhook_secret="whsec", session=FakeSession(*responses)), None


def test_razorpay_checks_the_payment_signature_the_way_checkout_signs_it():
    signature = hmac.new(b"s3cret", b"order_1|pay_1", hashlib.sha256).hexdigest()

    assert verify_payment_signature("order_1", "pay_1", signature, "s3cret") is True
    assert verify_payment_signature("order_1", "pay_2", signature, "s3cret") is False
    assert verify_payment_signature("order_1", "pay_1", signature, "other") is False
    assert verify_payment_signature("order_1", "pay_1", "", "s3cret") is False


def test_razorpay_creates_an_order_in_paise_with_auto_capture():
    session = FakeSession(FakeResponse(200, {"id": "order_AAA", "amount": 118000, "currency": "INR"}))
    gateway = RazorpayGateway("k", "s", "w", session)

    result = gateway.create(request(amount_minor=118000, customer=gw.GatewayCustomer(email="a@example.test", name="Asha")))

    method, url, kwargs = session.calls[0]
    assert (method, url) == ("POST", "https://api.razorpay.com/v1/orders") and kwargs["auth"] == ("k", "s")
    assert kwargs["json"]["amount"] == 118000 and kwargs["json"]["currency"] == "INR" and kwargs["json"]["payment_capture"] == 1
    assert result.gateway_order_id == "order_AAA" and result.status == gw.REQUIRES_PAYMENT_METHOD
    assert result.client_params == {"gateway": "razorpay", "key_id": "k", "order_id": "order_AAA", "amount": 118000, "currency": "INR", "name": "Asha", "email": "a@example.test"}
    manual = RazorpayGateway("k", "s", "w", FakeSession(FakeResponse(200, {"id": "o", "amount": 1, "currency": "INR"})))
    manual.create(request(capture_method="manual"))
    assert manual.session.calls[0][2]["json"]["payment_capture"] == 0


def test_razorpay_confirm_verifies_the_signature_then_reads_the_payment():
    signature = hmac.new(b"s", b"order_1|pay_1", hashlib.sha256).hexdigest()
    payment = {"id": "pay_1", "order_id": "order_1", "status": "captured", "method": "card", "fee": 2360, "card": {"network": "Visa", "last4": "4242"}}
    gateway = RazorpayGateway("k", "s", "w", FakeSession(FakeResponse(200, payment)))

    result = gateway.confirm(request(), {"razorpay_order_id": "order_1", "razorpay_payment_id": "pay_1", "razorpay_signature": signature})

    assert result.status == gw.SUCCEEDED and result.gateway_payment_id == "pay_1" and result.fee_minor == 2360
    assert (result.method_brand, result.method_last4) == ("visa", "4242")
    assert gateway.session.calls[0][1] == "https://api.razorpay.com/v1/payments/pay_1"


def test_razorpay_confirm_refuses_a_wrong_signature_or_another_orders_payment():
    gateway = RazorpayGateway("k", "s", "w", FakeSession())

    with pytest.raises(gw.GatewayError) as bad:
        gateway.confirm(request(), {"razorpay_order_id": "order_1", "razorpay_payment_id": "pay_1", "razorpay_signature": "forged"})
    with pytest.raises(gw.GatewayError) as other:
        gateway.confirm(request(), {"razorpay_order_id": "order_2", "razorpay_payment_id": "pay_1", "razorpay_signature": "x"})

    assert bad.value.code == "signature_mismatch" and other.value.code == "order_mismatch" and gateway.session.calls == []       # nothing was asked of Razorpay
    good = hmac.new(b"s", b"order_1|pay_9", hashlib.sha256).hexdigest()
    wrong_order = RazorpayGateway("k", "s", "w", FakeSession(FakeResponse(200, {"id": "pay_9", "order_id": "order_OTHER", "status": "captured"})))
    with pytest.raises(gw.GatewayError) as stolen:
        wrong_order.confirm(request(), {"razorpay_order_id": "order_1", "razorpay_payment_id": "pay_9", "razorpay_signature": good})
    assert stolen.value.code == "order_mismatch"


def test_razorpay_reads_a_failed_payment_and_picks_the_best_attempt_of_an_order():
    gateway = RazorpayGateway("k", "s", "w", FakeSession(FakeResponse(200, {"items": [
        {"id": "pay_a", "status": "failed", "error_code": "BAD_REQUEST_ERROR", "error_description": "Payment failed"},
        {"id": "pay_b", "status": "captured", "fee": 10, "method": "upi"}]})))

    result = gateway.fetch(request())

    assert result.status == gw.SUCCEEDED and result.gateway_payment_id == "pay_b" and result.method_brand == "upi"
    only_failed = RazorpayGateway("k", "s", "w", FakeSession(FakeResponse(200, {"items": [{"id": "pay_a", "status": "failed", "error_code": "X", "error_description": "No funds"}]})))
    failed = only_failed.fetch(request())
    assert failed.status == gw.FAILED and failed.failure_message == "No funds"
    assert RazorpayGateway("k", "s", "w", FakeSession(FakeResponse(200, {"items": []}))).fetch(request()).status == gw.REQUIRES_PAYMENT_METHOD


def test_razorpay_refunds_use_the_idempotency_key_and_read_the_outcome():
    session = FakeSession(FakeResponse(200, {"id": "rfnd_1", "status": "processed"}), FakeResponse(200, {"id": "rfnd_2", "status": "pending"}),
                          FakeResponse(400, {"error": {"code": "BAD_REQUEST_ERROR", "description": "The refund amount is more than the payment"}}))
    gateway = RazorpayGateway("k", "s", "w", session)

    done = gateway.refund(request(), 5000, "Cancelled", "refund-abc")
    pending = gateway.refund(request(), 5000, None, "refund-def")
    refused = gateway.refund(request(), 999999, None, "refund-ghi")

    assert (done.status, done.gateway_refund_id) == ("succeeded", "rfnd_1") and pending.status == "pending"
    assert refused.status == "failed" and "more than the payment" in refused.failure_message
    method, url, kwargs = session.calls[0]
    assert url == "https://api.razorpay.com/v1/payments/pay_1/refund" and kwargs["headers"] == {"X-Razorpay-Idempotency": "refund-abc"} and kwargs["json"]["amount"] == 5000


def test_razorpay_timeouts_and_missing_keys_are_reported_as_such():
    with pytest.raises(gw.GatewayTimeout):
        RazorpayGateway("k", "s", "w", FakeSession(requests.Timeout())).create(request())
    with pytest.raises(gw.GatewayError) as down:
        RazorpayGateway("k", "s", "w", FakeSession(FakeResponse(503, {}))).create(request())
    assert down.value.retryable is True
    with pytest.raises(gw.GatewayError) as unset:
        RazorpayGateway("", "", "", FakeSession()).create(request())
    assert unset.value.code == "not_configured"


def test_razorpay_webhooks_are_signed_and_mapped():
    body = json.dumps({"event": "payment.captured", "payload": {"payment": {"entity": {
        "id": "pay_1", "order_id": "order_1", "amount": 118000, "currency": "INR", "fee": 2360, "card": {"network": "MasterCard", "last4": "1111"}}}}}).encode()
    signature = hmac.new(b"whsec", body, hashlib.sha256).hexdigest()
    gateway = RazorpayGateway("k", "s", "whsec", FakeSession())

    [event] = gateway.parse_webhook({"X-Razorpay-Signature": signature, "X-Razorpay-Event-Id": "evt_9"}, body)

    assert (event.event_id, event.type, event.gateway_order_id, event.gateway_payment_id, event.amount_minor, event.fee_minor) == ("evt_9", "payment.succeeded", "order_1", "pay_1", 118000, 2360)
    assert event.method_brand == "mastercard" and event.method_last4 == "1111"
    with pytest.raises(gw.WebhookSignatureError):
        gateway.parse_webhook({"X-Razorpay-Signature": "forged"}, body)
    with pytest.raises(gw.WebhookSignatureError):
        RazorpayGateway("k", "s", "", FakeSession()).parse_webhook({"X-Razorpay-Signature": hmac.new(b"", body, hashlib.sha256).hexdigest()}, body)      # no secret set
    refund = json.dumps({"event": "refund.processed", "payload": {"refund": {"entity": {"id": "rfnd_1", "payment_id": "pay_1", "amount": 500, "currency": "INR"}}}}).encode()
    [ref_event] = gateway.parse_webhook({"X-Razorpay-Signature": hmac.new(b"whsec", refund, hashlib.sha256).hexdigest()}, refund)
    assert ref_event.type == "refund.succeeded" and ref_event.gateway_refund_id == "rfnd_1" and ref_event.gateway_payment_id == "pay_1"
    ignored = json.dumps({"event": "payout.processed", "payload": {}}).encode()
    assert gateway.parse_webhook({"X-Razorpay-Signature": hmac.new(b"whsec", ignored, hashlib.sha256).hexdigest()}, ignored) == []


# --- Stripe ---------------------------------------------------------------------------------------------------------------------------------

def stripe_header(body: bytes, secret="whsec_x", at=None):
    stamp = str(int(at if at is not None else time.time()))
    return f"t={stamp},v1=" + hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()


def test_stripe_signature_checks_the_body_the_secret_and_the_age():
    body = b'{"id":"evt_1"}'

    assert verify_signature(stripe_header(body), body, "whsec_x") is True
    assert verify_signature(stripe_header(body), body + b" ", "whsec_x") is False
    assert verify_signature(stripe_header(body), body, "whsec_other") is False
    assert verify_signature(stripe_header(body, at=time.time() - 3600), body, "whsec_x") is False                       # replayed an hour later
    assert verify_signature("", body, "whsec_x") is False and verify_signature("garbage", body, "whsec_x") is False
    assert verify_signature(stripe_header(body), body, "") is False


def test_stripe_form_encoding_nests_the_way_stripe_expects():
    assert form({"amount": 1000, "metadata": {"payment_id": "p1"}, "automatic_payment_methods": {"enabled": True}, "expand": ["a", "b"], "skip": None}) == {
        "amount": 1000, "metadata[payment_id]": "p1", "automatic_payment_methods[enabled]": "true", "expand": ["a", "b"]}


def intent(status="requires_payment_method", **extra):
    return {"id": "pi_1", "status": status, "client_secret": "pi_1_secret_x", "amount": 118000, "currency": "inr", **extra}


def test_stripe_creates_a_payment_intent_in_the_smallest_unit():
    session = FakeSession(FakeResponse(200, intent()))
    gateway = StripeGateway("sk_test_x", "whsec_x", session)

    result = gateway.create(request(amount_minor=118000, currency="INR", capture_method="manual", customer=gw.GatewayCustomer(email="a@example.test"), idempotency_key="pay-1-1"))

    method, url, kwargs = session.calls[0]
    assert (method, url) == ("POST", "https://api.stripe.com/v1/payment_intents")
    assert kwargs["headers"]["Authorization"] == "Bearer sk_test_x" and kwargs["headers"]["Idempotency-Key"] == "pay-1-1"
    data = kwargs["data"]
    assert data["amount"] == 118000 and data["currency"] == "inr" and data["capture_method"] == "manual" and data["automatic_payment_methods[enabled]"] == "true"
    assert data["metadata[payment_id]"] == "11111111-2222-3333-4444-555555555555"
    assert result.gateway_order_id == "pi_1" and result.client_params == {"gateway": "stripe", "client_secret": "pi_1_secret_x", "payment_intent_id": "pi_1"}


def test_stripe_charges_a_saved_method_straight_away():
    session = FakeSession(FakeResponse(200, intent("succeeded", latest_charge={"id": "ch_1", "payment_method_details": {"card": {"brand": "visa", "last4": "4242"}},
                                                                                "balance_transaction": {"fee": 3422}})))
    gateway = StripeGateway("sk", "wh", session)

    result = gateway.create(request(saved_token="pm_saved", customer=gw.GatewayCustomer(gateway_customer_id="cus_1")))

    data = session.calls[0][2]["data"]
    assert data["payment_method"] == "pm_saved" and data["customer"] == "cus_1" and data["confirm"] == "true" and "automatic_payment_methods[enabled]" not in data
    assert result.status == gw.SUCCEEDED and result.gateway_payment_id == "ch_1" and result.fee_minor == 3422 and (result.method_brand, result.method_last4) == ("visa", "4242")


def test_stripe_reads_every_state_of_an_intent():
    gateway = StripeGateway("sk", "wh", FakeSession(
        FakeResponse(200, intent("requires_action", next_action={"type": "redirect_to_url", "redirect_to_url": {"url": "https://hooks.stripe.com/3ds/1"}})),
        FakeResponse(200, intent("requires_action", next_action={"type": "use_stripe_sdk"})),
        FakeResponse(200, intent("requires_capture")),
        FakeResponse(200, intent("requires_payment_method", last_payment_error={"code": "card_declined", "message": "Your card was declined."})),
        FakeResponse(200, intent("canceled")),
        FakeResponse(200, intent("processing")),
    ))

    redirect, sdk, held, failed, cancelled, processing = (gateway.fetch(request()) for _ in range(6))

    assert redirect.status == gw.REQUIRES_ACTION and redirect.next_action == {"type": "redirect", "url": "https://hooks.stripe.com/3ds/1", "client_secret": "pi_1_secret_x"}
    assert sdk.next_action["type"] == "sdk" and held.status == gw.AUTHORIZED
    assert failed.status == gw.FAILED and failed.failure_code == "card_declined" and failed.failure_message == "Your card was declined."
    assert cancelled.status == gw.CANCELLED and processing.status == gw.PROCESSING
    assert gateway.session.calls[0][2]["params"] == {"expand": ["latest_charge.balance_transaction"]}


def test_stripe_confirm_refuses_another_orders_intent_and_refunds_map_their_status():
    gateway = StripeGateway("sk", "wh", FakeSession(FakeResponse(200, {"id": "re_1", "status": "succeeded"}), FakeResponse(200, {"id": "re_2", "status": "pending"}),
                                                   FakeResponse(402, {"error": {"code": "charge_already_refunded", "message": "Already refunded"}})))
    with pytest.raises(gw.GatewayError) as err:
        gateway.confirm(request(), {"payment_intent_id": "pi_OTHER"})

    ok, pending, refused = (gateway.refund(request(), 100, "reason", f"refund-{i}") for i in range(3))

    assert err.value.code == "order_mismatch"
    assert (ok.status, pending.status, refused.status) == ("succeeded", "pending", "failed") and refused.failure_message == "Already refunded"
    assert gateway.session.calls[0][2]["data"]["payment_intent"] == "order_1" and gateway.session.calls[0][2]["headers"]["Idempotency-Key"] == "refund-0"


def test_stripe_webhooks_are_verified_and_mapped():
    body = json.dumps({"id": "evt_5", "type": "payment_intent.succeeded", "data": {"object": {"id": "pi_1", "amount": 118000, "currency": "inr", "latest_charge": "ch_1"}}}).encode()
    gateway = StripeGateway("sk", "whsec_x", FakeSession())

    [event] = gateway.parse_webhook({"Stripe-Signature": stripe_header(body)}, body)

    assert (event.event_id, event.type, event.gateway_order_id, event.gateway_payment_id, event.amount_minor, event.currency) == ("evt_5", "payment.succeeded", "pi_1", "ch_1", 118000, "INR")
    with pytest.raises(gw.WebhookSignatureError):
        gateway.parse_webhook({"Stripe-Signature": stripe_header(body, secret="wrong")}, body)
    failed = json.dumps({"id": "evt_6", "type": "payment_intent.payment_failed", "data": {"object": {"id": "pi_1", "last_payment_error": {"code": "card_declined", "message": "No"}}}}).encode()
    [f_event] = gateway.parse_webhook({"Stripe-Signature": stripe_header(failed)}, failed)
    assert f_event.type == "payment.failed" and f_event.failure_code == "card_declined"
    refund = json.dumps({"id": "evt_7", "type": "refund.updated", "data": {"object": {"id": "re_1", "status": "succeeded", "payment_intent": "pi_1", "amount": 500, "currency": "inr"}}}).encode()
    [r_event] = gateway.parse_webhook({"Stripe-Signature": stripe_header(refund)}, refund)
    assert r_event.type == "refund.succeeded" and r_event.gateway_refund_id == "re_1" and r_event.gateway_order_id == "pi_1"
    other = json.dumps({"id": "evt_8", "type": "customer.created", "data": {"object": {}}}).encode()
    assert gateway.parse_webhook({"Stripe-Signature": stripe_header(other)}, other) == []


def test_stripe_saves_a_method_by_attaching_it_to_a_customer():
    gateway = StripeGateway("sk", "wh", FakeSession(FakeResponse(200, {"id": "cus_9"}), FakeResponse(200, {"id": "pm_1", "card": {"brand": "visa", "last4": "4242", "exp_month": 8, "exp_year": 2031}})))

    info = gateway.save_method(gw.GatewayCustomer(user_id="u1", email="a@example.test", name="Asha"), {"token": "pm_1"})

    assert (info.token, info.brand, info.last4, info.exp_month, info.exp_year, info.gateway_customer_id) == ("pm_1", "visa", "4242", 8, 2031, "cus_9")
    assert gateway.session.calls[1][1] == "https://api.stripe.com/v1/payment_methods/pm_1/attach" and gateway.session.calls[1][2]["data"] == {"customer": "cus_9"}
    with pytest.raises(gw.GatewayError):
        gateway.save_method(gw.GatewayCustomer(), {})


# --- the test gateway and the switch -----------------------------------------------------------------------------------------------------------

def test_the_gateway_is_chosen_by_the_setting(monkeypatch):
    from app.core.config import settings

    assert get_gateway("test").name == "test" and get_gateway("razorpay").name == "razorpay" and get_gateway("stripe").name == "stripe"
    monkeypatch.setattr(settings, "PAYMENT_GATEWAY", "razorpay")
    assert get_gateway().name == "razorpay"
    with pytest.raises(gw.GatewayError):
        get_gateway("paypal")


def test_the_test_gateway_knows_its_orders_and_refuses_what_makes_no_sense():
    gateway = TestGateway()
    created = gateway.create(request(amount_minor=1000, gateway_order_id=None))
    req = request(gateway_order_id=created.gateway_order_id, amount_minor=1000)

    assert gateway.fetch(req).status == gw.REQUIRES_PAYMENT_METHOD
    assert gateway.confirm(req, {"test_token": "tok_success"}).status == gw.SUCCEEDED
    assert gateway.refund(req, 600, None, "k1").status == "succeeded"
    assert gateway.refund(req, 600, None, "k2").status == "failed"                                              # only 400 is left
    assert gateway.fetch(request(gateway_order_id="unknown")).status == gw.FAILED
    with pytest.raises(gw.GatewayError):
        gateway.cancel(req)                                                                                     # already captured
    with pytest.raises(gw.GatewayError):
        gateway.capture(req)
    with pytest.raises(gw.GatewayTimeout):
        gateway.confirm(req, {"test_token": "tok_timeout"})


def test_the_test_gateway_is_refused_in_production(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    with pytest.raises(gw.GatewayError) as err:
        get_gateway("test")
    assert err.value.code == "not_configured" and "production" in err.value.message
    monkeypatch.setattr(settings, "PAYMENT_ALLOW_TEST_GATEWAY", True)
    assert get_gateway("test").name == "test"
    assert get_gateway("stripe").name == "stripe"
