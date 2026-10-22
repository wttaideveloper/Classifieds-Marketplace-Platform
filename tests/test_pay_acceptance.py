"""The acceptance criteria of the Payment, Multi-Currency and Taxation BRD (section 15), one test each, plus the non-functional
points that can be checked here (no card data, the same price shown and charged, everything leaves a trail)."""
import json
from decimal import Decimal
from uuid import UUID

from app.models.payment_model import FinanceAuditLog, Invoice, Payment, PaymentTransaction, SavedPaymentMethod, TaxTransaction
from tests.pay_support import TENANT_A, cart_order, india_gst, pay, post_rule, world  # noqa: F401


def test_1_a_customer_pays_in_a_non_base_currency_with_the_converted_amount_and_tax_shown_first(world):
    india_gst(world)
    priya = world.people.priya
    order = cart_order(world, priya, total="1000")
    body = {"purchase_type": "cart_order", "purchase_id": str(order.id), "currency": "USD", "billing": {"country": "IN", "state": "Karnataka"}}

    shown = world.as_user(priya).post("/payments/quote", json=body).json()
    created = world.as_user(priya).post("/payments", json=body).json()
    paid = world.as_user(priya).post(f"/payments/{created['payment']['id']}/verify", json={"test_token": "tok_success"}).json()

    assert shown["charge_currency"] == "USD" and shown["charge_total"] == "14.16" and shown["tax_amount"] == "180.00" and shown["charge_tax"] == "2.16"
    assert shown["exchange_rate"] == "0.0120000000"
    # what was shown is what was charged, to the last digit
    assert paid["status"] == "succeeded" and paid["charge_currency"] == "USD"
    for field in ("charge_subtotal", "charge_tax", "charge_total", "exchange_rate", "total_amount", "base_total"):
        assert paid[field] == shown[field], field
    assert created["checkout"]["amount"] == 1416 and created["checkout"]["currency"] == "USD"                    # the gateway is asked for exactly that


def test_2_tax_is_right_for_a_domestic_and_a_cross_border_case_of_each_configured_rule(world):
    india_gst(world)
    post_rule(world, tax_type="vat", name="VAT", country="GB", rate="20", reverse_charge_allowed=True)
    post_rule(world, tax_type="sales_tax", name="CA sales tax", country="US", state="California", rate="7.25", applies_to="product")
    post_rule(world, tax_type="vat", name="VAT", country="DE", rate="19", reverse_charge_allowed=True)
    priya = world.people.priya
    world.as_user(world.people.asha).put("/tax/merchant-profile", json={"country": "IN", "state": "Karnataka"})

    def tax(country, state=None, category="service", business=False, user=priya, tax_id=None):
        out = world.as_user(user).post("/tax/calculate", json={"currency": "INR", "items": [{"amount": "1000", "category": category}], "merchant_tenant_id": str(TENANT_A),
                                                               "customer": {"country": country, "state": state, "is_business": business, "tax_id": tax_id}})
        return out.json()

    assert tax("IN", "Karnataka")["tax_total"] == "180.00" and [b["name"] for b in tax("IN", "Karnataka")["breakdown"]] == ["CGST", "SGST"]       # domestic, same state
    assert tax("IN", "Kerala")["tax_total"] == "180.00" and [b["name"] for b in tax("IN", "Kerala")["breakdown"]] == ["IGST"]                    # domestic, other state
    assert tax("GB")["tax_total"] == "200.00"                                                                                                   # cross-border VAT, consumer
    assert tax("US", "California", "product")["tax_total"] == "72.50" and tax("US", "Texas", "product")["tax_total"] == "0.00"                    # state sales tax
    registered = world.as_user(priya).post("/tax/exemptions", json={"id_type": "vat", "number": "DE123456789", "country": "DE"}).json()
    world.as_user(world.people.root).patch(f"/tax/exemptions/{registered['id']}", json={"action": "approve"})
    business = tax("DE", business=True, tax_id="DE123456789")
    assert business["tax_total"] == "0.00" and business["reverse_charge"] is True                                                               # cross-border B2B: reverse charge
    assert tax("DE")["tax_total"] == "190.00"                                                                                                   # cross-border B2C: charged


def test_3_the_invoice_shows_the_currency_the_exchange_rate_and_the_tax_breakdown(world):
    india_gst(world)
    priya = world.people.priya
    payment = pay(world, priya, cart_order(world, priya, total="1000"), currency="EUR").json()

    invoice = world.as_user(priya).get(f"/invoices/{payment['invoice_id']}").json()
    pdf = world.as_user(priya).get(f"/invoices/{payment['invoice_id']}/download")

    assert invoice["currency"] == "EUR" and invoice["item_currency"] == "INR" and invoice["exchange_rate"] == "0.0110000000"
    assert [(r["name"], r["tax_amount"]) for r in invoice["tax_breakdown"]] == [("CGST", "0.99"), ("SGST", "0.99")]
    assert (invoice["subtotal"], invoice["tax_total"], invoice["total"]) == ("11.00", "1.98", "12.98") and invoice["base_total"] == "1180.00"
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_4_full_and_partial_refunds_reverse_the_payment_and_update_the_invoice_and_refund_records(world):
    india_gst(world)
    priya, asha = world.people.priya, world.people.asha
    partial = pay(world, priya, cart_order(world, priya, total="1000")).json()
    full = pay(world, priya, cart_order(world, priya, total="500")).json()

    one = world.as_user(asha).post("/refunds", json={"payment_id": partial["id"], "amount": "354"}).json()
    two = world.as_user(asha).post("/refunds", json={"payment_id": full["id"]}).json()

    assert one["status"] == "completed" and one["refund_type"] == "partial" and two["status"] == "completed" and two["refund_type"] == "full"
    shown = {p["id"]: p for p in world.as_user(priya).get("/payments").json()["items"]}
    assert shown[partial["id"]]["status"] == "partially_refunded" and shown[partial["id"]]["refunded_amount"] == "354.00" and shown[partial["id"]]["refundable_amount"] == "826.00"
    assert shown[full["id"]]["status"] == "refunded" and shown[full["id"]]["refundable_amount"] == "0.00"
    notes = world.as_user(priya).get("/invoices", params={"doc_type": "credit_note"}).json()["items"]
    assert sorted(n["total"] for n in notes) == ["354.00", "590.00"] and all(n["parent_invoice_id"] for n in notes)
    assert {n["id"] for n in notes} == {one["credit_note_id"], two["credit_note_id"]}
    # the tax comes back with the refund: 54 on the partial refund, all 90 on the full one
    world.db.expire_all()
    remaining = sum((t.tax_amount for t in world.db.query(TaxTransaction).all()), Decimal(0))
    assert remaining == Decimal("180.00") - Decimal("54.00")                                                        # 180 on the partial payment - 54 given back; the full one nets to 0
    refunds = {r["id"]: r for r in world.as_user(priya).get("/refunds").json()["items"]}
    assert refunds[one["id"]]["credit_note_id"] and refunds[two["id"]]["status_label"] == "Completed"


def test_5_the_settlement_report_reconciles_one_to_one_with_the_payments_and_refunds(world):
    india_gst(world)
    priya, ravi, asha, root = world.people.priya, world.people.ravi, world.people.asha, world.people.root
    a = pay(world, priya, cart_order(world, priya, total="1000")).json()
    pay(world, ravi, cart_order(world, ravi, total="2000"), currency="USD")
    pay(world, ravi, cart_order(world, ravi, total="3000"))
    world.as_user(asha).post("/refunds", json={"payment_id": a["id"], "amount": "236"})

    settled = world.as_user(root).post("/settlements/run", json={"tenant_id": str(TENANT_A)}).json()["settlement"]
    check = world.as_user(asha).get(f"/settlements/{settled['id']}/verify").json()
    report = world.as_user(asha).get("/payment-reports/settlements").json()

    assert check["reconciled"] is True and check["payments"] == 3 and check["refunds"] == 1 and check["items_checked"] == 4
    detail = world.as_user(asha).get(f"/settlements/{settled['id']}").json()
    payments = {p.id: p for p in world.db.query(Payment).filter(Payment.settlement_id == UUID(settled["id"])).all()}
    assert len(payments) == 3 and {str(i["payment_id"]) for i in detail["items"] if i["kind"] == "payment"} == {str(k) for k in payments}
    # the report's totals are the sums of the items, and the net is gross - refunds - commission - fees
    items = detail["items"]
    gross = sum(Decimal(i["gross"]) for i in items if i["kind"] == "payment")
    refunds = -sum(Decimal(i["gross"]) for i in items if i["kind"] == "refund")
    commission = sum(Decimal(i["commission"]) for i in items)
    fees = sum(Decimal(i["fee"]) + Decimal(i["fixed_fee"]) for i in items)
    row = report["rows"][0]
    assert (Decimal(row["gross_amount"]), Decimal(row["refund_amount"]), Decimal(row["commission_amount"])) == (gross, refunds, commission)
    assert Decimal(row["net_amount"]) == gross - refunds - commission - fees


def test_nothing_the_api_returns_or_stores_contains_card_data(world):
    india_gst(world)
    priya = world.people.priya
    saved = world.as_user(priya).post("/payment-methods", json={"token": "tok_saved_visa_4242", "exp_month": 12, "exp_year": 2030}).json()
    payment = pay(world, priya, cart_order(world, priya), saved_method_id=saved["id"]).json()
    texts = [json.dumps(world.as_user(priya).get(path).json()) for path in ("/payment-methods", f"/payments/{payment['id']}", "/payments", "/invoices")]
    texts.append(world.as_user(priya).get(f"/invoices/{payment['invoice_id']}/download").content.decode("latin-1"))

    assert saved["last4"] == "4242" and "token" not in saved
    for text in texts:
        assert "tok_saved" not in text and "4242424242424242" not in text and "cvv" not in text.lower()
    row = world.db.query(SavedPaymentMethod).one()
    assert row.token == "tok_saved_visa_4242" and not hasattr(row, "card_number") and not hasattr(row, "cvv")                      # only the gateway's token and safe details
    stored = " ".join(str(v) for t in world.db.query(PaymentTransaction).all() for v in (t.detail, t.error, t.gateway_ref))
    assert "tok_success" not in stored and "4242424242424242" not in stored


def test_every_financial_change_leaves_a_trail(world):
    india_gst(world)
    root, asha, priya = world.people.root, world.people.asha, world.people.priya
    world.as_user(root).post("/currencies/rates", json={"quote_currency": "USD", "rate": "0.0125"})
    world.as_user(asha).put("/settlements/config", json={"schedule": "daily"})
    payment = pay(world, priya, cart_order(world, priya)).json()
    world.as_user(asha).post("/refunds", json={"payment_id": payment["id"], "amount": "100"})
    world.as_user(root).post("/settlements/run", json={"tenant_id": str(TENANT_A), "force": True})

    actions = {e.action for e in world.db.query(FinanceAuditLog).all()}

    assert {"tax_rule_created", "merchant_tax_profile_saved", "exchange_rate_set", "settlement_config_saved", "refund_approved", "refund_completed", "settlement_created"} <= actions
    trail = world.as_user(asha).get(f"/payments/{payment['id']}/transactions").json()["items"]
    assert [t["kind"] for t in trail] == ["create", "verify"]
    assert world.db.query(TaxTransaction).count() == 4                                                                           # the tax of the payment and of the refund
    assert world.db.query(Invoice).count() == 2                                                                                  # the invoice and the credit note


def test_the_price_is_never_taken_from_the_client_and_a_repeat_does_not_charge_twice(world):
    india_gst(world)
    priya = world.people.priya
    order = cart_order(world, priya, total="1000")
    body = {"purchase_type": "cart_order", "purchase_id": str(order.id), "amount": "1", "charge_total": "1", "billing": {"country": "IN", "state": "Karnataka"}}

    first = world.as_user(priya).post("/payments", json=body, headers={"Idempotency-Key": "once"}).json()
    again = world.as_user(priya).post("/payments", json=body, headers={"Idempotency-Key": "once"}).json()

    assert first["payment"]["charge_total"] == "1180.00" and again["payment"]["id"] == first["payment"]["id"] and world.db.query(Payment).count() == 1
