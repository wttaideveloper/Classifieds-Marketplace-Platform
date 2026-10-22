"""Payments: pricing, creating, verifying (incl. 3-D Secure), idempotency, retry, cancel, capture, timeouts, webhooks, risk,
saved methods and who can see what. Runs against the test gateway."""
import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.core.config import settings
from app.models.cart_model import Order
from app.models.event_aux_models import EventOrder
from app.models.payment_model import ExchangeRate, Invoice, Payment, PaymentTransaction, TaxTransaction, WebhookEvent
from app.services import payment_service
from app.services.payment_gateways import test_gateway
from tests.pay_support import TENANT_A, TENANT_B, cart_order, event_order, india_gst, notices_of, pay, post_rule, world  # noqa: F401

API = "/payments"


def create(world, user, order, **extra):
    body = {"purchase_type": "cart_order", "purchase_id": str(order.id), "billing": {"country": "IN", "state": "Karnataka"}, **extra}
    return world.as_user(user).post(API, json=body)


def payment_of(world, payment_id):
    world.db.expire_all()
    return world.db.get(Payment, __import__("uuid").UUID(payment_id))


def sign_post(world, body: dict, *, secret_ok=True):
    raw = json.dumps(body).encode()
    signature = test_gateway.sign(raw) if secret_ok else "bad"
    return world.as_user(None).post(f"{API}/webhook/test", content=raw, headers={"X-Test-Signature": signature, "Content-Type": "application/json"})


# --- the price shown before paying ---------------------------------------------------------------------------------------------

def test_the_quote_shows_items_tax_exchange_rate_and_the_final_amount(world):
    india_gst(world)
    order = cart_order(world, world.people.priya, total="1000")

    body = {"purchase_type": "cart_order", "purchase_id": str(order.id), "currency": "USD", "billing": {"country": "IN", "state": "Karnataka"}}
    quote = world.as_user(world.people.priya).post(f"{API}/quote", json=body).json()

    assert quote["item_currency"] == "INR" and quote["charge_currency"] == "USD" and quote["exchange_rate"] == "0.0120000000"
    assert quote["subtotal_amount"] == "1000.00" and quote["tax_amount"] == "180.00" and quote["total_amount"] == "1180.00"
    assert quote["charge_subtotal"] == "12.00" and quote["charge_tax"] == "2.16" and quote["charge_total"] == "14.16"
    assert quote["base_currency"] == "INR" and quote["base_total"] == "1180.00"
    assert [(r["name"], r["tax_amount"]) for r in quote["tax_breakdown"]] == [("CGST", "1.08"), ("SGST", "1.08")]
    assert quote["lines"][0]["description"] == "Yoga mat" and quote["lines"][0]["charge_gross_amount"] == "14.16"
    assert world.db.query(Payment).count() == 0                         # nothing is saved by a quote


def test_a_quote_needs_a_country_and_a_supported_currency(world):
    order = cart_order(world, world.people.priya, country="")
    body = {"purchase_type": "cart_order", "purchase_id": str(order.id)}

    assert world.as_user(world.people.priya).post(f"{API}/quote", json=body).status_code == 422
    assert world.as_user(world.people.priya).post(f"{API}/quote", json={**body, "billing": {"country": "IN"}, "currency": "GBP"}).status_code == 422
    assert world.as_user(world.people.priya).post(f"{API}/quote", json={**body, "billing": {"country": "India"}}).status_code == 422


def test_the_country_of_the_shipping_address_is_used_when_none_is_sent(world):
    india_gst(world)
    order = cart_order(world, world.people.priya, total="500")

    quote = world.as_user(world.people.priya).post(f"{API}/quote", json={"purchase_type": "cart_order", "purchase_id": str(order.id)}).json()

    assert quote["tax_amount"] == "90.00"                                # India and Karnataka came from the address


# --- paying ----------------------------------------------------------------------------------------------------------------------------

def test_a_cart_order_is_paid_end_to_end(world):
    india_gst(world)
    priya = world.people.priya
    order = cart_order(world, priya, total="1000")

    created = create(world, priya, order)
    assert created.status_code == 201
    body = created.json()
    payment = body["payment"]
    assert payment["status"] == "created" and payment["status_label"] == "Waiting for payment" and payment["charge_total"] == "1180.00"
    assert body["checkout"]["gateway"] == "test" and body["checkout"]["amount"] == 118000 and "tok_success" in body["checkout"]["test_tokens"]
    assert payment["actions"] == ["verify", "checkout", "cancel"] and payment["risk"] is None            # the customer does not see the risk score

    paid = world.as_user(priya).post(f"{API}/{payment['id']}/verify", json={"test_token": "tok_success"}).json()

    assert paid["status"] == "succeeded"
    assert paid["method_brand"] == "visa" and paid["method_last4"] == "4242" and paid["captured_at"] and paid["invoice_id"]
    assert "download_invoice" in paid["actions"] and "refund" in paid["actions"]
    world.db.expire_all()
    assert world.db.get(Order, order.id).status == "confirmed"                                          # the order moved on
    row = payment_of(world, payment["id"])
    assert row.gateway_fee == Decimal("23.6000") and row.attempt_no == 1
    taxes = world.db.query(TaxTransaction).filter_by(payment_id=row.id).all()
    assert sorted((t.name, str(t.tax_amount)) for t in taxes) == [("CGST", "90.0000"), ("SGST", "90.0000")]
    invoice = world.db.query(Invoice).filter_by(payment_id=row.id).one()
    assert invoice.number.startswith("INV-") and invoice.total == Decimal("1180.0000") and invoice.currency == "INR"
    got = [n for n in world.notices if n["category"] != "tax_rule_updated"]
    assert [n["category"] for n in got] == ["payment_succeeded", "payment_received", "invoice_generated"]
    assert str(got[0]["user_ids"][0]) == priya["id"] and str(got[1]["user_ids"][0]) == world.people.asha["id"]
    assert "INR 1,180.00" in got[0]["message"] and "card" not in got[0]["message"].lower()


def test_paying_in_another_currency_locks_the_rate_and_keeps_both_amounts(world):
    india_gst(world)
    priya = world.people.priya
    order = cart_order(world, priya, total="1000")

    paid = pay(world, priya, order, currency="USD").json()

    assert paid["charge_currency"] == "USD" and paid["item_currency"] == "INR" and paid["exchange_rate"] == "0.0120000000"
    assert paid["charge_subtotal"] == "12.00" and paid["charge_tax"] == "2.16" and paid["charge_total"] == "14.16" and paid["total_amount"] == "1180.00"
    assert paid["base_currency"] == "INR" and paid["base_total"] == "1180.00"
    # the rate moves; what was charged does not
    world.db.add(ExchangeRate(base_currency="INR", quote_currency="USD", rate=Decimal("0.02"), source="manual"))
    world.db.commit()
    again = world.as_user(priya).get(f"{API}/{paid['id']}").json()
    assert again["exchange_rate"] == "0.0120000000" and again["charge_total"] == "14.16"
    invoice = world.db.query(Invoice).filter_by(payment_id=__import__("uuid").UUID(paid["id"])).one()
    assert invoice.currency == "USD" and invoice.item_currency == "INR" and invoice.exchange_rate == Decimal("0.0120000000") and invoice.total == Decimal("14.1600")


def test_a_zero_decimal_currency_is_charged_in_whole_units(world):
    india_gst(world)
    order = cart_order(world, world.people.priya, total="1000")

    created = create(world, world.people.priya, order, currency="JPY").json()

    assert created["payment"]["charge_total"] == "2124" and created["checkout"]["amount"] == 2124       # 1180 INR x 1.8 yen, no minor units
    kwd = cart_order(world, world.people.ravi, total="1000")
    assert create(world, world.people.ravi, kwd, currency="KWD").json()["checkout"]["amount"] == 4366    # 4.366 dinar in fils


def test_the_amount_comes_from_the_order_never_from_the_request(world):
    india_gst(world)
    order = cart_order(world, world.people.priya, total="1000")

    created = create(world, world.people.priya, order, amount="1", charge_total="1", total="1")

    assert created.status_code == 201 and created.json()["payment"]["charge_total"] == "1180.00"


def test_only_the_owner_can_pay_an_order_and_only_a_pending_one(world):
    india_gst(world)
    order = cart_order(world, world.people.priya)

    assert create(world, world.people.ravi, order).status_code == 403
    assert create(world, world.people.asha, order).status_code == 403
    assert world.as_user(None).post(API, json={"purchase_type": "cart_order", "purchase_id": str(order.id)}).status_code == 401
    for status_name in ("confirmed", "cancelled"):
        other = cart_order(world, world.people.priya, status=status_name)
        assert create(world, world.people.priya, other).status_code == 409
    assert world.as_user(world.people.priya).post(API, json={"purchase_type": "cart_order", "purchase_id": "not-an-id"}).status_code == 422
    assert world.as_user(world.people.priya).post(API, json={"purchase_type": "cart_order", "purchase_id": "9f8a1b2c-0000-4000-8000-000000000001"}).status_code == 404


def test_an_order_that_is_already_paid_cannot_be_paid_again(world):
    india_gst(world)
    order = cart_order(world, world.people.priya)
    pay(world, world.people.priya, order)
    world.db.query(Order).update({"status": "pending"})
    world.db.commit()

    again = create(world, world.people.priya, order)

    assert again.status_code == 409 and "already paid" in again.json()["detail"]


def test_an_event_order_is_paid_and_confirmed(world):
    post_rule(world, rate="5")
    priya = world.people.priya
    order = event_order(world, priya, amount="2000")

    paid = pay(world, priya, order).json()

    assert paid["status"] == "succeeded" and paid["charge_total"] == "2100.00" and paid["lines"][0]["description"].startswith("Event registration")
    world.db.expire_all()
    assert world.db.get(EventOrder, order.id).payment_status == "confirmed"
    assert create_event_payment(world, world.people.ravi, order).status_code == 403                       # another person's registration
    already = event_order(world, priya, payment_status="confirmed")
    assert create_event_payment(world, priya, already).status_code == 409


def create_event_payment(world, user, order):
    return world.as_user(user).post(API, json={"purchase_type": "event_order", "purchase_id": str(order.id), "billing": {"country": "IN"}})


def test_a_payment_below_the_currency_minimum_or_above_the_maximum_is_refused(world):
    world.as_user(world.people.root).patch("/currencies/USD", json={"min_amount": "5", "max_amount": "100"})
    order = cart_order(world, world.people.priya, total="100")                                            # about 1.2 USD
    big = cart_order(world, world.people.ravi, total="20000")                                             # about 240 USD

    low = create(world, world.people.priya, order, currency="USD")
    high = create(world, world.people.ravi, big, currency="USD")

    assert low.status_code == 422 and "smallest payment in USD" in low.json()["detail"]
    assert high.status_code == 422 and "largest payment in USD" in high.json()["detail"]
    assert create(world, world.people.priya, order).status_code == 201                                    # INR has no limit set


def test_stale_exchange_rates_stop_a_foreign_currency_payment(world):
    order = cart_order(world, world.people.priya)
    world.db.query(ExchangeRate).filter_by(quote_currency="USD").update({"created_at": datetime.utcnow() - timedelta(hours=100)})
    world.db.commit()

    stale = create(world, world.people.priya, order, currency="USD")

    assert stale.status_code == 503 and "out of date" in stale.json()["detail"]
    assert create(world, world.people.priya, order).status_code == 201                                    # the order's own currency needs no rate


# --- idempotency and resuming ------------------------------------------------------------------------------------------------------

def test_the_same_idempotency_key_returns_the_same_payment(world):
    india_gst(world)
    priya = world.people.priya
    order = cart_order(world, priya)
    body = {"purchase_type": "cart_order", "purchase_id": str(order.id), "billing": {"country": "IN", "state": "Karnataka"}}

    first = world.as_user(priya).post(API, json=body, headers={"Idempotency-Key": "checkout-1"}).json()
    second = world.as_user(priya).post(API, json=body, headers={"Idempotency-Key": "checkout-1"}).json()

    assert first["payment"]["id"] == second["payment"]["id"] and first["checkout"] == second["checkout"] and world.db.query(Payment).count() == 1
    other = cart_order(world, priya)
    clash = world.as_user(priya).post(API, json={**body, "purchase_id": str(other.id)}, headers={"Idempotency-Key": "checkout-1"})
    assert clash.status_code == 409
    assert world.as_user(priya).post(API, json=body, headers={"Idempotency-Key": "k" * 61}).status_code == 422
    # another person's identical key is a different key
    ravi_order = cart_order(world, world.people.ravi)
    other_user = world.as_user(world.people.ravi).post(API, json={**body, "purchase_id": str(ravi_order.id)}, headers={"Idempotency-Key": "checkout-1"})
    assert other_user.status_code == 201


def test_coming_back_to_an_unpaid_payment_returns_it_and_a_new_currency_starts_again(world):
    priya = world.people.priya
    order = cart_order(world, priya)

    first = create(world, priya, order).json()["payment"]
    back = create(world, priya, order).json()["payment"]
    assert back["id"] == first["id"] and world.db.query(Payment).count() == 1

    usd = create(world, priya, order, currency="USD").json()["payment"]
    assert usd["id"] != first["id"] and usd["charge_currency"] == "USD"
    assert payment_of(world, first["id"]).status == "cancelled"


# --- failure, retry, 3-D Secure -----------------------------------------------------------------------------------------------------------

def test_a_declined_card_fails_the_payment_with_a_reason_and_can_be_retried(world):
    india_gst(world)
    priya = world.people.priya
    order = cart_order(world, priya)

    failed = pay(world, priya, order, token="tok_fail").json()

    assert failed["status"] == "failed" and failed["failure_code"] == "card_declined" and failed["failure_message"] == "The card was declined"
    assert failed["actions"] == ["retry"] and failed["invoice_id"] is None
    [notice] = notices_of(world, "payment_failed")
    assert notice["metadata"]["can_retry"] is True and "did not go through" in notice["message"]
    world.db.expire_all()
    assert world.db.get(Order, order.id).status == "pending"

    retried = world.as_user(priya).post(f"{API}/{failed['id']}/retry", json={})
    assert retried.status_code == 201 and retried.json()["payment"]["attempt_no"] == 2 and retried.json()["payment"]["retry_of_id"] == failed["id"]
    ok = world.as_user(priya).post(f"{API}/{retried.json()['payment']['id']}/verify", json={"test_token": "tok_success"}).json()
    assert ok["status"] == "succeeded"
    history = world.as_user(priya).get(API, params={"purchase_id": str(order.id)}).json()
    assert [(p["attempt_no"], p["status"]) for p in history["items"]][::-1] == [(1, "failed"), (2, "succeeded")]


def test_only_a_failed_payment_can_be_retried_and_only_by_the_payer_and_only_so_many_times(world, monkeypatch):
    priya = world.people.priya
    order = cart_order(world, priya)
    first = pay(world, priya, order, token="tok_fail").json()

    assert world.as_user(world.people.ravi).post(f"{API}/{first['id']}/retry", json={}).status_code == 404
    assert world.as_user(world.people.asha).post(f"{API}/{first['id']}/retry", json={}).status_code == 403
    current = first
    monkeypatch.setattr(settings, "PAYMENT_MAX_ATTEMPTS", 3)
    for _ in range(2):
        retried = world.as_user(priya).post(f"{API}/{current['id']}/retry", json={}).json()["payment"]
        current = world.as_user(priya).post(f"{API}/{retried['id']}/verify", json={"test_token": "tok_insufficient"}).json()
        assert current["status"] == "failed" and current["failure_code"] == "insufficient_funds"
    assert world.as_user(priya).post(f"{API}/{current['id']}/retry", json={}).status_code == 429
    fresh = pay(world, priya, cart_order(world, priya), token=None).json()["payment"]
    assert world.as_user(priya).post(f"{API}/{fresh['id']}/retry", json={}).status_code == 409           # not failed


def test_3d_secure_asks_for_a_step_and_then_completes_or_fails(world):
    priya = world.people.priya
    order = cart_order(world, priya)
    created = create(world, priya, order).json()["payment"]

    stepped = world.as_user(priya).post(f"{API}/{created['id']}/verify", json={"test_token": "tok_3ds"}).json()
    assert stepped["status"] == "requires_action" and stepped["next_action"]["type"] == "redirect" and stepped["next_action"]["method"] == "3ds"
    assert stepped["invoice_id"] is None and "verify" in stepped["actions"]

    done = world.as_user(priya).post(f"{API}/{created['id']}/verify", json={"test_token": "tok_3ds", "authenticated": True}).json()
    assert done["status"] == "succeeded" and done["next_action"] is None

    other = create(world, world.people.ravi, cart_order(world, world.people.ravi)).json()["payment"]
    world.as_user(world.people.ravi).post(f"{API}/{other['id']}/verify", json={"test_token": "tok_3ds"})
    refused = world.as_user(world.people.ravi).post(f"{API}/{other['id']}/verify", json={"test_token": "tok_3ds", "authenticated": False}).json()
    assert refused["status"] == "failed" and refused["failure_code"] == "authentication_failed"


def test_verifying_twice_does_not_pay_twice(world):
    india_gst(world)
    priya = world.people.priya
    paid = pay(world, priya, cart_order(world, priya)).json()

    again = world.as_user(priya).post(f"{API}/{paid['id']}/verify", json={"test_token": "tok_success"}).json()

    assert again["status"] == "succeeded" and world.db.query(Invoice).count() == 1 and world.db.query(TaxTransaction).count() == 2
    assert len(notices_of(world, "payment_succeeded")) == 1


# --- cancel and capture ---------------------------------------------------------------------------------------------------------------------

def test_an_unpaid_payment_can_be_cancelled_and_a_paid_one_cannot(world):
    priya = world.people.priya
    created = create(world, priya, cart_order(world, priya)).json()["payment"]

    cancelled = world.as_user(priya).post(f"{API}/{created['id']}/cancel").json()
    assert cancelled["status"] == "cancelled" and cancelled["cancelled_at"]
    assert world.as_user(priya).post(f"{API}/{created['id']}/cancel").json()["status"] == "cancelled"        # again: nothing changes
    paid = pay(world, priya, cart_order(world, priya)).json()
    assert world.as_user(priya).post(f"{API}/{paid['id']}/cancel").status_code == 409
    assert world.as_user(world.people.ravi).post(f"{API}/{created['id']}/cancel").status_code == 404


def custom(world, merchant=None, customer=None, **extra):
    body = {"purchase_type": "custom", "customer_user_id": (customer or world.people.priya)["id"], "item_currency": "INR", "description": "Consultation",
            "items": [{"description": "30 minute consultation", "amount": "1000", "quantity": 1, "category": "service"}], "billing": {"country": "IN", "state": "Karnataka"}, **extra}
    return world.as_user(merchant or world.people.asha).post(API, json=body)


def test_a_business_can_hold_the_money_first_and_capture_it_later(world):
    india_gst(world)
    asha, priya = world.people.asha, world.people.priya
    made = custom(world, capture_method="manual")
    assert made.status_code == 201, made.text
    payment_id = made.json()["payment"]["id"]

    held = world.as_user(priya).post(f"{API}/{payment_id}/verify", json={"test_token": "tok_success"}).json()
    assert held["status"] == "authorized" and held["invoice_id"] is None and held["captured_at"] is None
    assert world.db.query(Invoice).count() == 0 and "capture" not in held["actions"]
    assert world.as_user(priya).post(f"{API}/{payment_id}/capture").status_code == 403
    assert world.as_user(world.people.bela).post(f"{API}/{payment_id}/capture").status_code == 404

    captured = world.as_user(asha).post(f"{API}/{payment_id}/capture").json()
    assert captured["status"] == "succeeded" and captured["invoice_id"] and captured["captured_at"]
    assert world.as_user(asha).post(f"{API}/{payment_id}/capture").status_code == 409


def test_an_authorized_payment_can_be_cancelled_before_capture(world):
    asha, priya = world.people.asha, world.people.priya
    payment_id = custom(world, capture_method="manual").json()["payment"]["id"]
    world.as_user(priya).post(f"{API}/{payment_id}/verify", json={"test_token": "tok_success"})

    cancelled = world.as_user(asha).post(f"{API}/{payment_id}/cancel").json()

    assert cancelled["status"] == "cancelled" and world.db.query(Invoice).count() == 0


# --- custom payments (a business asks a customer) --------------------------------------------------------------------------------------------

def test_a_business_asks_a_customer_to_pay_and_only_that_customer_can(world):
    india_gst(world)
    asha, priya = world.people.asha, world.people.priya
    made = custom(world)
    payment = made.json()["payment"]

    assert payment["customer_user_id"] == priya["id"] and payment["tenant_id"] == str(TENANT_A) and payment["tax_amount"] == "180.00"
    assert made.json()["checkout"]["gateway"] == "test"
    assert world.as_user(asha).get(f"{API}/{payment['id']}/checkout").status_code == 403               # the business cannot take the customer's payment
    assert world.as_user(world.people.ravi).get(f"{API}/{payment['id']}").status_code == 404
    assert world.as_user(priya).get(f"{API}/{payment['id']}/checkout").json()["checkout"]["order_id"] == made.json()["checkout"]["order_id"]
    paid = world.as_user(priya).post(f"{API}/{payment['id']}/verify", json={"test_token": "tok_success"}).json()
    assert paid["status"] == "succeeded"
    assert world.as_user(priya).get(f"{API}/{payment['id']}/checkout").status_code == 409              # nothing left to open


def test_who_may_ask_for_a_custom_payment(world):
    assert custom(world, merchant=world.people.priya).status_code == 403
    assert custom(world, merchant=world.people.root).status_code == 422                                  # which business?
    assert custom(world, merchant=world.people.root, tenant_id=str(TENANT_B)).status_code == 201
    assert world.as_user(world.people.asha).post(API, json={"purchase_type": "custom", "item_currency": "INR", "items": []}).status_code == 422
    assert world.as_user(world.people.asha).post(API, json={"purchase_type": "custom", "item_currency": "INR", "items": [{"description": "x", "amount": "5"}]}).status_code == 422
    assert custom(world, items=[{"description": "free", "amount": "0"}]).status_code == 422             # nothing to pay


# --- the gateway does not answer ----------------------------------------------------------------------------------------------------------------

def test_a_gateway_timeout_marks_the_payment_for_checking_and_the_check_finds_the_money(world):
    india_gst(world)
    priya = world.people.priya
    created = create(world, priya, cart_order(world, priya)).json()["payment"]

    timed_out = world.as_user(priya).post(f"{API}/{created['id']}/verify", json={"test_token": "tok_timeout"}).json()

    assert timed_out["status"] == "processing" and timed_out["needs_reconciliation"] is True and "did not answer in time" in timed_out["failure_message"]
    assert world.db.query(PaymentTransaction).filter_by(kind="timeout").count() == 1
    # the gateway did take the money
    row = payment_of(world, created["id"])
    test_gateway.ORDERS[row.gateway_order_id].update({"status": "succeeded", "gateway_payment_id": "test_pay_found"})
    summary = payment_service.reconcile_pending(world.db)

    assert summary["paid"] == 1
    world.db.expire_all()
    row = world.db.get(Payment, row.id)
    assert row.status == "succeeded" and row.needs_reconciliation is False and row.invoice_id and row.failure_message is None
    assert len(notices_of(world, "payment_succeeded")) == 1


def test_a_payment_nobody_paid_expires_after_the_check(world):
    priya = world.people.priya
    created = create(world, priya, cart_order(world, priya)).json()["payment"]
    row = payment_of(world, created["id"])
    row.expires_at = datetime.utcnow() - timedelta(minutes=1)
    world.db.commit()

    summary = payment_service.reconcile_pending(world.db)

    assert summary["expired"] == 1 and payment_of(world, created["id"]).status == "expired"
    assert world.as_user(priya).get(f"{API}/{created['id']}").json()["actions"] == ["retry"]


def test_money_arriving_after_a_payment_was_closed_is_held_for_review_and_can_be_refunded(world):
    india_gst(world)
    priya = world.people.priya
    created = create(world, priya, cart_order(world, priya)).json()["payment"]
    world.as_user(priya).post(f"{API}/{created['id']}/cancel")
    row = payment_of(world, created["id"])
    event = {"id": "evt_late_1", "type": "payment.succeeded", "data": {"order_id": row.gateway_order_id, "payment_id": "test_pay_late", "amount": 118000, "currency": "INR"}}

    assert sign_post(world, event).json()["processed"] == 1

    held = payment_of(world, created["id"])
    assert held.status == "review" and held.failure_code == "late_payment" and held.needs_reconciliation is True
    assert notices_of(world, "payment_review_required")
    refused = world.as_user(world.people.asha).post(f"{API}/{created['id']}/resolve", json={"action": "approve"})
    assert refused.status_code == 403
    test_gateway.ORDERS[row.gateway_order_id].update({"status": "succeeded", "gateway_payment_id": "test_pay_late"})
    done = world.as_user(world.people.root).post(f"{API}/{created['id']}/resolve", json={"action": "reject", "note": "Order was cancelled"}).json()
    assert done["status"] == "refunded" and Decimal(done["refunded_amount"]) == Decimal("1180.00")


# --- webhooks -----------------------------------------------------------------------------------------------------------------------------------------

def test_a_signed_webhook_completes_the_payment_and_is_applied_once(world):
    india_gst(world)
    priya = world.people.priya
    created = create(world, priya, cart_order(world, priya)).json()["payment"]
    row = payment_of(world, created["id"])
    event = {"id": "evt_1", "type": "payment.succeeded", "data": {"order_id": row.gateway_order_id, "payment_id": "test_pay_w1", "amount": 118000, "currency": "INR", "fee": 2360}}

    first = sign_post(world, event)
    again = sign_post(world, event)

    assert first.status_code == 200 and first.json() == {"received": 1, "processed": 1, "duplicates": 0, "ignored": 0}
    assert again.json() == {"received": 1, "processed": 0, "duplicates": 1, "ignored": 0}
    done = payment_of(world, created["id"])
    assert done.status == "succeeded" and done.gateway_payment_id == "test_pay_w1" and done.gateway_fee == Decimal("23.6000") and done.invoice_id
    assert world.db.query(Invoice).count() == 1 and len(notices_of(world, "payment_succeeded")) == 1 and world.db.query(WebhookEvent).count() == 1


def test_a_webhook_for_a_failed_payment_and_an_unknown_one(world):
    priya = world.people.priya
    created = create(world, priya, cart_order(world, priya)).json()["payment"]
    row = payment_of(world, created["id"])

    failed = sign_post(world, {"id": "evt_f", "type": "payment.failed", "data": {"order_id": row.gateway_order_id, "failure_code": "card_declined", "failure_message": "Declined"}})
    unknown = sign_post(world, {"id": "evt_u", "type": "payment.succeeded", "data": {"order_id": "nope", "amount": 1}})

    assert failed.json()["processed"] == 1 and payment_of(world, created["id"]).status == "failed"
    assert unknown.status_code == 200 and unknown.json()["ignored"] == 1                                 # acknowledged so the gateway stops retrying


def test_a_webhook_with_the_wrong_amount_is_held_not_accepted(world):
    priya = world.people.priya
    created = create(world, priya, cart_order(world, priya)).json()["payment"]
    row = payment_of(world, created["id"])

    sign_post(world, {"id": "evt_amt", "type": "payment.succeeded", "data": {"order_id": row.gateway_order_id, "payment_id": "p", "amount": 100}})

    held = payment_of(world, created["id"])
    assert held.status == "review" and held.failure_code == "amount_mismatch" and held.invoice_id is None


def test_webhooks_with_a_bad_signature_or_for_another_gateway_are_refused(world):
    body = {"id": "evt_x", "type": "payment.succeeded", "data": {}}

    assert sign_post(world, body, secret_ok=False).status_code == 401
    assert world.as_user(None).post(f"{API}/webhook/stripe", content=b"{}", headers={"Stripe-Signature": "t=1,v1=2"}).status_code == 404         # not the gateway in use
    assert world.as_user(None).post(f"{API}/webhook/test", content=b"not json", headers={"X-Test-Signature": test_gateway.sign(b"not json")}).status_code == 400
    assert world.db.query(WebhookEvent).count() == 0


def test_a_second_payment_for_an_order_that_is_already_paid_is_held_as_a_duplicate(world):
    india_gst(world)
    priya = world.people.priya
    order = cart_order(world, priya)
    first = create(world, priya, order).json()["payment"]
    world.as_user(priya).post(f"{API}/{first['id']}/verify", json={"test_token": "tok_fail"})            # failed, so another attempt is allowed
    second = world.as_user(priya).post(f"{API}/{first['id']}/retry", json={}).json()["payment"]
    world.as_user(priya).post(f"{API}/{second['id']}/verify", json={"test_token": "tok_success"})
    # the customer succeeds on the first checkout after all
    row = payment_of(world, first["id"])
    sign_post(world, {"id": "evt_dup", "type": "payment.succeeded", "data": {"order_id": row.gateway_order_id, "payment_id": "dup", "amount": 118000}})

    assert payment_of(world, first["id"]).status == "review" and payment_of(world, first["id"]).failure_code == "duplicate_payment"
    assert world.db.query(Invoice).count() == 1


# --- risk ---------------------------------------------------------------------------------------------------------------------------------------------

def test_a_risky_payment_is_held_for_a_super_admin_who_can_let_it_through(world, monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_RISK_HIGH_AMOUNT_BASE", 500.0)
    monkeypatch.setattr(settings, "PAYMENT_RISK_REVIEW_SCORE", 30)
    priya = world.people.priya
    order = cart_order(world, priya, total="1000")

    held = create(world, priya, order)

    assert held.status_code == 201 and held.json()["payment"]["status"] == "review" and held.json()["checkout"] == {}
    payment_id = held.json()["payment"]["id"]
    assert payment_of(world, payment_id).risk_score == 30 and payment_of(world, payment_id).risk_reasons == ["high_amount"]
    assert notices_of(world, "payment_review_required")
    assert world.as_user(priya).post(f"{API}/{payment_id}/verify", json={}).status_code == 409
    seen_by_business = world.as_user(world.people.asha).get(f"{API}/{payment_id}").json()
    assert seen_by_business["risk"] == {"score": 30, "decision": "review", "reasons": ["high_amount"]}

    approved = world.as_user(world.people.root).post(f"{API}/{payment_id}/resolve", json={"action": "approve", "note": "Known customer"}).json()
    assert approved["status"] == "created"
    assert world.as_user(priya).get(f"{API}/{payment_id}/checkout").json()["checkout"]["gateway"] == "test"
    assert world.as_user(priya).post(f"{API}/{payment_id}/verify", json={"test_token": "tok_success"}).json()["status"] == "succeeded"


def test_a_rejected_review_fails_the_payment_and_a_very_risky_one_is_refused_outright(world, monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_RISK_HIGH_AMOUNT_BASE", 500.0)
    monkeypatch.setattr(settings, "PAYMENT_RISK_REVIEW_SCORE", 30)
    priya = world.people.priya
    held = create(world, priya, cart_order(world, priya, total="1000")).json()["payment"]

    rejected = world.as_user(world.people.root).post(f"{API}/{held['id']}/resolve", json={"action": "reject", "note": "Looks stolen"}).json()
    assert rejected["status"] == "failed" and rejected["failure_code"] == "review_rejected"
    assert world.as_user(priya).post(f"{API}/{held['id']}/retry", json={}).status_code == 201                  # a person said no to *that* attempt; a new one is risk-checked again

    monkeypatch.setattr(settings, "PAYMENT_RISK_DENY_SCORE", 30)
    denied = create(world, world.people.ravi, cart_order(world, world.people.ravi, total="1000"))
    assert denied.status_code == 403 and "security check" in denied.json()["detail"]
    row = world.db.query(Payment).filter_by(failure_code="risk_denied").one()
    assert row.status == "failed" and row.gateway_order_id is None                                              # the gateway was never asked


def test_repeated_failures_raise_the_score(world, monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_RISK_REVIEW_SCORE", 30)
    priya = world.people.priya
    for _ in range(3):
        pay(world, priya, cart_order(world, priya), token="tok_fail")

    fourth = create(world, priya, cart_order(world, priya)).json()["payment"]

    assert fourth["status"] == "review" and payment_of(world, fourth["id"]).risk_reasons == ["repeated_failed_payments"]


def test_only_a_super_admin_resolves_a_review(world, monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_RISK_HIGH_AMOUNT_BASE", 500.0)
    monkeypatch.setattr(settings, "PAYMENT_RISK_REVIEW_SCORE", 30)
    held = create(world, world.people.priya, cart_order(world, world.people.priya, total="1000")).json()["payment"]

    for user in (world.people.priya, world.people.asha):
        assert world.as_user(user).post(f"{API}/{held['id']}/resolve", json={"action": "approve"}).status_code == 403
    fine = pay(world, world.people.ravi, cart_order(world, world.people.ravi, total="100"), token=None).json()["payment"]
    assert world.as_user(world.people.root).post(f"{API}/{fine['id']}/resolve", json={"action": "approve"}).status_code == 409        # not under review


# --- saved payment methods --------------------------------------------------------------------------------------------------------------------------

def test_a_saved_method_stores_only_a_token_and_the_safe_details(world):
    priya = world.people.priya

    saved = world.as_user(priya).post("/payment-methods", json={"token": "tok_saved_visa_4242", "exp_month": 12, "exp_year": 2030, "holder_name": "Priya S"})

    assert saved.status_code == 201
    body = saved.json()
    assert body["brand"] == "visa" and body["last4"] == "4242" and body["exp_month"] == 12 and body["is_default"] is True and body["is_expired"] is False
    assert "token" not in body
    second = world.as_user(priya).post("/payment-methods", json={"token": "tok_saved_mastercard_1111", "set_default": True}).json()
    listing = world.as_user(priya).get("/payment-methods").json()
    assert [m["last4"] for m in listing["items"]] == ["1111", "4242"] and second["is_default"] is True and listing["items"][1]["is_default"] is False
    assert world.as_user(world.people.ravi).get("/payment-methods").json()["total"] == 0
    assert world.as_user(priya).post("/payment-methods", json={"token": "tok_saved_visa_4242"}).status_code == 409


def test_card_numbers_are_never_accepted(world):
    priya = world.people.priya

    assert world.as_user(priya).post("/payment-methods", json={"token": "4242424242424242"}).status_code == 422
    assert world.as_user(priya).post("/payment-methods", json={"token": "tok_saved_visa_4242", "card_number": "4242424242424242", "cvv": "123"}).status_code == 422
    assert world.as_user(priya).post("/payment-methods", json={"token": "not-a-test-token"}).status_code == 422
    assert world.as_user(None).post("/payment-methods", json={"token": "tok_saved_visa_4242"}).status_code == 401
    from app.models.payment_model import SavedPaymentMethod

    assert world.db.query(SavedPaymentMethod).count() == 0


def test_paying_with_a_saved_method_needs_no_card_entry(world):
    india_gst(world)
    priya = world.people.priya
    method = world.as_user(priya).post("/payment-methods", json={"token": "tok_saved_visa_4242"}).json()
    order = cart_order(world, priya)

    created = create(world, priya, order, saved_method_id=method["id"])

    assert created.status_code == 201 and created.json()["payment"]["status"] == "succeeded" and created.json()["payment"]["method_last4"] == "4242"
    assert created.json()["payment"]["invoice_id"] is not None
    stranger = cart_order(world, world.people.ravi)
    assert create(world, world.people.ravi, stranger, saved_method_id=method["id"]).status_code == 404            # someone else's method


def test_default_and_removal_of_saved_methods(world):
    priya = world.people.priya
    a = world.as_user(priya).post("/payment-methods", json={"token": "tok_saved_visa_4242"}).json()
    b = world.as_user(priya).post("/payment-methods", json={"token": "tok_saved_visa_1881"}).json()

    assert world.as_user(priya).put(f"/payment-methods/{b['id']}/default").json()["is_default"] is True
    assert world.as_user(priya).delete(f"/payment-methods/{b['id']}").status_code == 200
    left = world.as_user(priya).get("/payment-methods").json()["items"]
    assert [(m["id"], m["is_default"]) for m in left] == [(a["id"], True)]                                      # the default moves on
    assert world.as_user(world.people.ravi).delete(f"/payment-methods/{a['id']}").status_code == 404
    assert world.as_user(priya).put(f"/payment-methods/{b['id']}/default").status_code == 404


# --- history and who sees what ----------------------------------------------------------------------------------------------------------------------

def test_payment_history_is_scoped_by_role_and_filterable(world):
    india_gst(world)
    p_order, r_order, b_order = cart_order(world, world.people.priya), cart_order(world, world.people.ravi), cart_order(world, world.people.priya, tenant=TENANT_B)
    first = pay(world, world.people.priya, p_order).json()
    pay(world, world.people.ravi, r_order, token="tok_fail")
    third = pay(world, world.people.priya, b_order, currency="USD").json()

    mine = world.as_user(world.people.priya).get(API).json()
    assert mine["total"] == 2 and {p["id"] for p in mine["items"]} == {first["id"], third["id"]}
    business = world.as_user(world.people.asha).get(API).json()
    assert business["total"] == 2 and {p["purchase_id"] for p in business["items"]} == {str(p_order.id), str(r_order.id)}      # its own business only
    assert world.as_user(world.people.root).get(API).json()["total"] == 3
    assert world.as_user(world.people.root).get(API, params={"tenant_id": str(TENANT_B)}).json()["total"] == 1
    assert world.as_user(world.people.root).get(API, params={"status": "failed"}).json()["total"] == 1
    assert world.as_user(world.people.root).get(API, params={"currency": "usd"}).json()["total"] == 1
    assert world.as_user(world.people.priya).get(API, params={"page_size": 1, "page": 2}).json()["items"][0]["id"] in (first["id"], third["id"])
    assert world.as_user(world.people.ravi).get(f"{API}/{first['id']}").status_code == 404
    assert world.as_user(world.people.bela).get(f"{API}/{first['id']}").status_code == 404
    assert world.as_user(world.people.asha).get(f"{API}/{first['id']}").status_code == 200
    assert world.as_user(None).get(API).status_code == 401


def test_the_gateway_trail_is_for_the_business_and_the_platform_only(world):
    paid = pay(world, world.people.priya, cart_order(world, world.people.priya)).json()

    trail = world.as_user(world.people.asha).get(f"{API}/{paid['id']}/transactions")

    assert trail.status_code == 200 and [t["kind"] for t in trail.json()["items"]] == ["create", "verify"]
    assert world.as_user(world.people.priya).get(f"{API}/{paid['id']}/transactions").status_code == 403
    assert world.as_user(world.people.bela).get(f"{API}/{paid['id']}/transactions").status_code == 404


def test_a_reminder_is_sent_once_for_a_payment_that_failed_and_was_not_retried(world):
    priya = world.people.priya
    failed = pay(world, priya, cart_order(world, priya), token="tok_fail").json()
    row = payment_of(world, failed["id"])
    row.failed_at = datetime.utcnow() - timedelta(hours=30)
    world.db.commit()

    assert payment_service.send_retry_reminders(world.db) == 1
    assert payment_service.send_retry_reminders(world.db) == 0
    [notice] = notices_of(world, "payment_retry_reminder")
    assert notice["metadata"]["can_retry"] is True and str(notice["user_ids"][0]) == priya["id"]
    retried = pay(world, priya, cart_order(world, priya), token="tok_fail").json()
    row = payment_of(world, retried["id"])
    row.failed_at = datetime.utcnow() - timedelta(hours=30)
    world.db.commit()
    world.as_user(priya).post(f"{API}/{retried['id']}/retry", json={})
    assert payment_service.send_retry_reminders(world.db) == 0                                                  # they did try again


def test_gateway_trouble_at_creation_fails_the_payment_cleanly(world, monkeypatch):
    from app.services.payment_gateways import base as gw
    from app.services.payment_gateways.test_gateway import TestGateway

    monkeypatch.setattr(TestGateway, "create", lambda self, request: (_ for _ in ()).throw(gw.GatewayTimeout()))
    priya = world.people.priya

    resp = create(world, priya, cart_order(world, priya))

    assert resp.status_code == 504 and "not available" in resp.json()["detail"]
    row = world.db.query(Payment).one()
    assert row.status == "failed" and row.failure_code == "gateway_timeout"


def test_a_failed_custom_payment_is_retried_from_what_it_kept(world):
    india_gst(world)
    asha, priya = world.people.asha, world.people.priya
    payment_id = custom(world).json()["payment"]["id"]
    failed = world.as_user(priya).post(f"{API}/{payment_id}/verify", json={"test_token": "tok_fail"}).json()
    assert failed["status"] == "failed"

    retried = world.as_user(priya).post(f"{API}/{payment_id}/retry", json={})

    assert retried.status_code == 201
    second = retried.json()["payment"]
    assert second["attempt_no"] == 2 and second["charge_total"] == "1180.00" and second["customer_user_id"] == priya["id"] and second["tenant_id"] == str(TENANT_A)
    assert second["lines"][0]["description"] == "30 minute consultation" and second["billing"]["country"] == "IN"
    done = world.as_user(priya).post(f"{API}/{second['id']}/verify", json={"test_token": "tok_success"}).json()
    assert done["status"] == "succeeded" and world.as_user(asha).get(f"{API}/{done['id']}").json()["retry_of_id"] == payment_id


def test_a_refunded_event_order_is_marked_refunded(world):
    post_rule(world, rate="5")
    priya = world.people.priya
    order = event_order(world, priya, amount="2000")
    paid = pay(world, priya, order).json()

    refund = world.as_user(world.people.asha).post("/refunds", json={"payment_id": paid["id"]}).json()

    assert refund["status"] == "completed" and refund["amount"] == "2100.00"
    world.db.expire_all()
    stored = world.db.get(EventOrder, order.id)
    assert stored.payment_status == "refunded" and stored.status == "refunded"


def test_the_payment_methods_of_the_payer_are_used_only_with_the_gateway_they_belong_to(world, monkeypatch):
    priya = world.people.priya
    method = world.as_user(priya).post("/payment-methods", json={"token": "tok_saved_visa_4242"}).json()
    monkeypatch.setattr(settings, "PAYMENT_GATEWAY", "stripe")
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "")

    resp = create(world, priya, cart_order(world, priya), saved_method_id=method["id"])

    assert resp.status_code == 409 and "another payment gateway" in resp.json()["detail"]
