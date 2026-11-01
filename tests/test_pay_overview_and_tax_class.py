"""The Commerce overview, the read-only gateway status, the typed report documentation, and a product's tax class driving its tax rule."""
import json
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.core.config import settings
from app.models.cart_model import Order, OrderItem
from app.models.payment_model import ExchangeRate, Refund
from app.models.product_model import Product
from tests.pay_support import TENANT_A, TENANT_B, cart_order, india_gst, pay, post_rule, world  # noqa: F401

OVERVIEW = "/payment-reports/overview"
GATEWAY = "/finance/gateway-status"


def sale(world, user=None, total="1000", tenant=TENANT_A, **extra):
    user = user or world.people.priya
    return pay(world, user, cart_order(world, user, total=total, tenant=tenant), **extra).json()


def setup_sales(world):
    """The same three sales as the finance report tests, one partly refunded."""
    india_gst(world)
    a = sale(world)
    b = sale(world, world.people.ravi, total="2000", currency="USD")
    c = sale(world, world.people.ravi, total="500", tenant=TENANT_B)
    world.as_user(world.people.asha).post("/refunds", json={"payment_id": a["id"], "amount": "590"})
    return a, b, c


# ---------------------------------------------------------------- overview

def test_the_platform_sees_every_business_in_one_call(world):
    setup_sales(world)

    body = world.as_user(world.people.root).get(OVERVIEW).json()

    assert body["base_currency"] == "INR" and body["period"] == {"date_from": None, "date_to": None}
    pay_ = body["payments"]
    assert (pay_["total"], pay_["paid"], pay_["waiting"], pay_["failed"], pay_["under_review"]) == (3, 3, 0, 0, 0)
    assert pay_["by_status"]["partially_refunded"] == 1 and pay_["by_status"]["succeeded"] == 2 and pay_["success_rate_percent"] == 100.0
    assert (pay_["base_collected"], pay_["base_refunded"], pay_["base_net"]) == ("4130.00", "590.00", "3540.00")
    assert body["refunds"]["completed"] == 1 and body["refunds"]["open"] == 0 and body["refunds"]["base_refunded"] == "590.00"
    assert body["tax"] == {"base_collected": "630.00", "base_refunded": "90.00", "base_net": "540.00"}
    assert body["settlements"]["total"] == 0 and body["settlements"]["awaiting_settlement"] == {"payments": 3, "base_amount": "4130.00"}
    assert body["needs_attention"]["open_reconciliation_mismatches"] == 0


def test_a_business_sees_only_its_own_figures(world):
    setup_sales(world)

    mine = world.as_user(world.people.asha).get(OVERVIEW).json()
    other = world.as_user(world.people.bela).get(OVERVIEW).json()
    narrowed = world.as_user(world.people.asha).get(OVERVIEW, params={"tenant_id": str(TENANT_B)}).json()                # ignored for a business
    root_narrowed = world.as_user(world.people.root).get(OVERVIEW, params={"tenant_id": str(TENANT_B)}).json()

    assert mine["payments"]["total"] == 2 and mine["payments"]["base_collected"] == "3540.00" and mine["needs_attention"]["open_reconciliation_mismatches"] is None
    assert other["payments"]["total"] == 1 and other["payments"]["base_collected"] == "590.00"
    assert narrowed["payments"]["total"] == 2 and root_narrowed["payments"]["total"] == 1


def test_open_overdue_and_failed_things_are_counted_for_attention(world):
    a, _b, _c = setup_sales(world)
    failed = pay(world, world.people.priya, cart_order(world, world.people.priya, total="300"), token="tok_fail")
    assert failed.status_code in (200, 402, 409)
    waiting = world.as_user(world.people.ravi).post("/payments", json={"purchase_type": "cart_order", "purchase_id": str(cart_order(world, world.people.ravi, total="400").id),
                                                                       "billing": {"country": "IN", "state": "Karnataka"}})
    assert waiting.status_code == 201
    request = world.as_user(world.people.priya).post("/refunds", json={"payment_id": sale(world, total="700")["id"], "reason": "Changed my mind"})
    assert request.status_code == 201 and request.json()["status"] == "requested"
    world.db.query(Refund).filter_by(status="requested").update({"sla_due_at": datetime.utcnow() - timedelta(hours=1)})
    world.db.commit()

    body = world.as_user(world.people.root).get(OVERVIEW).json()

    assert body["payments"]["failed"] == 1 and body["payments"]["waiting"] == 1 and body["payments"]["total"] == 6
    assert body["refunds"]["open"] == 1 and body["refunds"]["overdue"] == 1 and body["refunds"]["base_open_amount"] != "0.00"
    assert body["needs_attention"]["refunds_overdue"] == 1 and body["payments"]["success_rate_percent"] == round(4 * 100 / 6, 1)


def test_the_overview_can_be_limited_to_a_period_and_is_not_for_customers(world):
    setup_sales(world)

    future = world.as_user(world.people.root).get(OVERVIEW, params={"date_from": (datetime.utcnow() + timedelta(days=1)).isoformat()}).json()

    assert future["payments"]["total"] == 0 and future["payments"]["success_rate_percent"] is None and future["payments"]["base_collected"] == "0.00"
    assert future["refunds"]["total"] == 0 and future["settlements"]["awaiting_settlement"] == {"payments": 0, "base_amount": "0.00"}
    assert world.as_user(world.people.priya).get(OVERVIEW).status_code == 403
    assert world.as_user(None).get(OVERVIEW).status_code == 401


def test_the_overview_counts_settlements(world):
    setup_sales(world)
    world.as_user(world.people.root).post("/settlements/run", json={"tenant_id": str(TENANT_A), "force": True})

    body = world.as_user(world.people.root).get(OVERVIEW).json()

    assert body["settlements"]["total"] == 1 and body["settlements"]["pending"] == 1 and body["settlements"]["by_currency"][0]["currency"] == "INR"
    assert Decimal(body["settlements"]["by_currency"][0]["pending_net"]) > 0 and body["settlements"]["awaiting_settlement"]["payments"] == 1


# ---------------------------------------------------------------- gateway status

def test_the_gateway_status_never_shows_a_key_and_says_what_is_missing(world, monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_GATEWAY", "stripe")
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_TOPSECRET123")
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_TOPSECRET456")
    monkeypatch.setattr(settings, "STRIPE_PUBLISHABLE_KEY", "")
    monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", "rzp_live_RAZORSECRET")
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", "razorsecretvalue")
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "https://api.example.test")

    resp = world.as_user(world.people.root).get(GATEWAY)

    body = resp.json()
    assert resp.status_code == 200 and body["editable"] is False and body["active_gateway"] == "stripe" and body["active_mode"] == "test"
    providers = {p["provider"]: p for p in body["providers"]}
    assert providers["stripe"]["active"] is True and providers["stripe"]["configured"] is True and providers["stripe"]["mode"] == "test"
    assert providers["stripe"]["webhook_secret_set"] is True and providers["stripe"]["publishable_key_set"] is False
    assert providers["stripe"]["webhook_url"] == "https://api.example.test/api/v1/payments/webhook/stripe" and "payment_intent.succeeded" in providers["stripe"]["webhook_events"]
    assert providers["razorpay"]["active"] is False and providers["razorpay"]["mode"] == "live" and providers["razorpay"]["webhook_secret_set"] is False
    text = json.dumps(body)
    for secret in ("TOPSECRET", "RAZORSECRET", "razorsecretvalue"):
        assert secret not in text
    assert any("publishable key" in w for w in body["warnings"])
    assert body["exchange_rates"]["rates_loaded"] == 5 and body["tax"]["active_rules"] == 0 and any("tax rules" in w for w in body["warnings"])
    assert body["jobs"]["interval_seconds"] == settings.PAYMENT_JOBS_INTERVAL_SECONDS and body["limits"]["refund_sla_hours"] == settings.REFUND_SLA_HOURS


def test_a_selected_gateway_without_keys_is_flagged_and_the_test_gateway_is_labelled(world, monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "")
    monkeypatch.setattr(settings, "PAYMENT_GATEWAY", "stripe")
    missing = world.as_user(world.people.root).get(GATEWAY).json()
    assert missing["active_mode"] is None and any("keys are not set" in w for w in missing["warnings"])

    monkeypatch.setattr(settings, "PAYMENT_GATEWAY", "test")
    test = world.as_user(world.people.root).get(GATEWAY).json()
    assert test["active_gateway"] == "test" and any("test gateway" in w.lower() for w in test["warnings"])
    world.db.query(ExchangeRate).delete()
    world.db.commit()
    assert any("exchange rates" in w for w in world.as_user(world.people.root).get(GATEWAY).json()["warnings"])


def test_only_the_platform_reads_the_gateway_status(world):
    for user in (world.people.asha, world.people.priya):
        assert world.as_user(user).get(GATEWAY).status_code == 403
    assert world.as_user(None).get(GATEWAY).status_code == 401


# ---------------------------------------------------------------- typed report documentation

def test_the_report_endpoints_are_typed_in_swagger(world):
    from app.main import app

    spec = app.openapi()
    expected = {"/api/v1/payment-reports/payments": "PaymentReportOut", "/api/v1/payment-reports/refunds": "RefundReportOut",
                "/api/v1/payment-reports/settlements": "SettlementReportOut", "/api/v1/payment-reports/currency": "CurrencyReportOut",
                "/api/v1/payment-reports/exchange-rates": "ExchangeRateReportOut", "/api/v1/payment-reports/reconciliation": "ReconciliationReportOut",
                "/api/v1/tax/reports": "TaxReportOut"}
    for path, model in expected.items():
        schema = spec["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["$ref"].endswith(model), path
        assert spec["components"]["schemas"][model].get("example"), model
    assert spec["paths"]["/api/v1/payment-reports/overview"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("CommerceOverview")
    assert spec["paths"]["/api/v1/finance/gateway-status"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("GatewayStatus")


def test_the_documented_report_shape_matches_the_real_one(world):
    from app.schemas.payment_report_schema import CurrencyReportOut, PaymentReportOut, RefundReportOut, SettlementReportOut

    setup_sales(world)
    root = world.as_user(world.people.root)
    world.as_user(world.people.root).post("/settlements/run", json={"tenant_id": str(TENANT_A), "force": True})

    for path, model in (("payments", PaymentReportOut), ("refunds", RefundReportOut), ("settlements", SettlementReportOut), ("currency", CurrencyReportOut)):
        real = world.as_user(world.people.root).get(f"/payment-reports/{path}").json()
        parsed = model.model_validate(real)                                                 # raises if a documented field is missing or has the wrong type
        assert parsed.model_dump(exclude_none=False) is not None
    assert root is not None


# ---------------------------------------------------------------- a product's tax class

def product(world, tax_class, price=1000.0):
    from app.models.enterprise_model import Enterprise

    enterprise = world.db.query(Enterprise).filter_by(tenant_id=TENANT_A).first()
    row = Product(tenant_id=TENANT_A, enterprise_id=enterprise.id, product_name=f"Item {uuid4().hex[:4]}", product_category="Health", product_price=price, status="published", tax_class=tax_class)
    world.db.add(row)
    world.db.commit()
    return row


def order_with(world, user, *entries, tenant=TENANT_A):
    """A pending order whose lines point at real products: entries are (product, quantity)."""
    total = sum(p.product_price * q for p, q in entries)
    order = Order(user_id=__import__("uuid").UUID(user["id"]), tenant_id=tenant, status="pending", total=total, currency="INR", shipping_country="India", shipping_state="Karnataka",
                  shipping_full_name=user["name"])
    world.db.add(order)
    world.db.flush()
    for p, q in entries:
        world.db.add(OrderItem(order_id=order.id, product_id=p.id, product_name=p.product_name, quantity=q, unit_price=p.product_price, line_total=p.product_price * q, currency="INR"))
    world.db.commit()
    return order


def rate_rules(world):
    """General product tax of 18% (CGST + SGST 9 each) and a reduced class of 5% (one rule)."""
    for name, rate in (("CGST", "9"), ("SGST", "9")):
        post_rule(world, name=name, rate=rate, applies_to="product", condition="intra_state")
    post_rule(world, name="GST reduced", rate="5", applies_to="reduced", condition="intra_state")
    assert world.as_user(world.people.asha).put("/tax/merchant-profile", json={"country": "IN", "state": "Karnataka"}).status_code == 200


def test_a_products_tax_class_selects_its_own_rule_over_the_category_rule(world):
    rate_rules(world)
    plain, reduced = product(world, None), product(world, "Reduced")
    order = order_with(world, world.people.priya, (plain, 1), (reduced, 1))

    created = world.as_user(world.people.priya).post("/payments", json={"purchase_type": "cart_order", "purchase_id": str(order.id), "billing": {"country": "IN", "state": "Karnataka"}})

    lines = {l["description"]: l for l in created.json()["payment"]["lines"]}
    assert created.status_code == 201
    assert lines[plain.product_name]["tax_amount"] == "180.00" and lines[plain.product_name]["tax_class"] is None
    assert lines[reduced.product_name]["tax_amount"] == "50.00" and lines[reduced.product_name]["tax_class"] == "reduced"           # 5%, not 18%
    assert created.json()["payment"]["tax_amount"] == "230.00" and created.json()["payment"]["total_amount"] == "2230.00"
    names = {t["name"] for t in lines[reduced.product_name]["taxes"]}
    assert names == {"GST reduced"}


def test_a_tax_class_with_no_rule_falls_back_to_the_category_rule(world):
    rate_rules(world)
    odd = product(world, "no-such-class")
    order = order_with(world, world.people.priya, (odd, 1))

    body = world.as_user(world.people.priya).post("/payments", json={"purchase_type": "cart_order", "purchase_id": str(order.id), "billing": {"country": "IN", "state": "Karnataka"}}).json()

    assert body["payment"]["tax_amount"] == "180.00" and body["payment"]["lines"][0]["tax_class"] == "no-such-class"


def test_the_tax_calculation_and_a_custom_payment_accept_a_tax_class(world):
    rate_rules(world)
    calc = world.as_user(world.people.priya).post("/tax/calculate", json={
        "currency": "INR", "customer": {"country": "IN", "state": "Karnataka"}, "merchant_tenant_id": str(TENANT_A),
        "items": [{"description": "Vitamin", "amount": "1000", "category": "product", "tax_class": "Reduced"}, {"description": "Mat", "amount": "1000", "category": "product"}]})

    assert calc.status_code == 200
    assert [l["tax_amount"] for l in calc.json()["lines"]] == ["50.00", "180.00"]

    custom = world.as_user(world.people.asha).post("/payments", json={
        "purchase_type": "custom", "currency": "INR", "item_currency": "INR", "customer_user_id": world.people.ravi["id"], "billing": {"country": "IN", "state": "Karnataka"},
        "items": [{"description": "Supplement", "amount": "200", "category": "product", "tax_class": "reduced"}]})
    assert custom.status_code == 201 and custom.json()["payment"]["tax_amount"] == "10.00"


def test_a_retry_keeps_the_tax_class(world):
    rate_rules(world)
    reduced = product(world, "reduced")
    order = order_with(world, world.people.priya, (reduced, 1))
    failed = pay(world, world.people.priya, order, token="tok_fail")
    payment_id = failed.json()["id"] if "id" in failed.json() else failed.json()["payment"]["id"]

    retry = world.as_user(world.people.priya).post(f"/payments/{payment_id}/retry", json={})

    assert retry.status_code == 201 and retry.json()["payment"]["tax_amount"] == "50.00"
