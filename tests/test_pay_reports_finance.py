"""Finance reports (payments, refunds, settlements, currency, exchange rates, tax, reconciliation), the gateway-report
reconciliation, the audit log, the scheduled jobs and the accounting export."""
import json
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.models.payment_model import ExchangeRate, FinanceAuditLog, Payment, ReconciliationItem
from app.services import currency_service
from app.services.payment_gateways import test_gateway
from tests.pay_support import TENANT_A, TENANT_B, cart_order, india_gst, notices_of, pay, world  # noqa: F401

REPORTS = "/payment-reports"


def sale(world, user=None, total="1000", tenant=TENANT_A, **extra):
    user = user or world.people.priya
    return pay(world, user, cart_order(world, user, total=total, tenant=tenant), **extra).json()


def setup_sales(world):
    """Three sales for business A (one in USD, one partly refunded) and one for business B."""
    india_gst(world)
    a = sale(world)
    b = sale(world, world.people.ravi, total="2000", currency="USD")
    c = sale(world, world.people.ravi, total="500", tenant=TENANT_B)
    world.as_user(world.people.asha).post("/refunds", json={"payment_id": a["id"], "amount": "590"})
    return a, b, c


# --- payment, refund, settlement and currency reports ------------------------------------------------------------------------------------

def test_the_payment_report_is_scoped_and_totals_in_the_base_currency(world):
    a, b, c = setup_sales(world)

    mine = world.as_user(world.people.asha).get(f"{REPORTS}/payments").json()
    everything = world.as_user(world.people.root).get(f"{REPORTS}/payments").json()

    assert mine["total_rows"] == 2 and everything["total_rows"] == 3 and mine["base_currency"] == "INR"
    assert mine["totals"] == {"payments": 2, "paid": 2, "failed": 0, "base_collected": "3540.00", "base_refunded": "590.00", "base_tax": "540.00"}
    row = next(r for r in mine["rows"] if r["charge_currency"] == "USD")
    assert row["item_currency"] == "INR" and row["charge_total"] == "28.32" and row["exchange_rate"] == "0.0120000000" and row["base_total"] == "2360.00"
    assert row["invoice_number"].startswith("INV-") and row["gateway_fee"] is not None and row["method"] == "visa 4242"
    assert everything["totals"]["base_collected"] == "4130.00"
    assert world.as_user(world.people.root).get(f"{REPORTS}/payments", params={"tenant_id": str(TENANT_B)}).json()["total_rows"] == 1
    assert world.as_user(world.people.root).get(f"{REPORTS}/payments", params={"currency": "usd"}).json()["total_rows"] == 1
    assert world.as_user(world.people.root).get(f"{REPORTS}/payments", params={"status": "partially_refunded"}).json()["total_rows"] == 1
    assert world.as_user(world.people.root).get(f"{REPORTS}/payments", params={"date_from": (datetime.utcnow() + timedelta(days=1)).isoformat()}).json()["total_rows"] == 0


def test_reports_are_for_businesses_and_the_platform_only(world):
    setup_sales(world)

    for path in ("payments", "refunds", "settlements", "currency", "exchange-rates", "reconciliation"):
        assert world.as_user(world.people.priya).get(f"{REPORTS}/{path}").status_code == 403, path
        assert world.as_user(None).get(f"{REPORTS}/{path}").status_code == 401, path
    for path in ("exchange-rates", "reconciliation"):
        assert world.as_user(world.people.asha).get(f"{REPORTS}/{path}").status_code == 403, path               # platform only


def test_the_payment_report_downloads_as_csv(world):
    setup_sales(world)

    resp = world.as_user(world.people.asha).get(f"{REPORTS}/payments", params={"format": "csv"})

    lines = resp.text.strip().splitlines()
    assert resp.headers["content-type"].startswith("text/csv") and resp.headers["content-disposition"] == 'attachment; filename="payment-report.csv"'
    assert lines[0].startswith("payment_id,created_at") and len(lines) == 3 and "INV-" in resp.text and "28.32" in resp.text


def test_a_csv_cell_that_looks_like_a_formula_is_kept_as_text(world):
    from app.services import payment_export

    out = payment_export.csv_response([{"a": "=HYPERLINK(\"http://evil\")", "b": "-5", "c": "+91 99999", "d": "plain"}], ["a", "b", "c", "d"], "x.csv")

    cells = out.body.decode().splitlines()[1]
    assert cells.startswith("\"'=HYPERLINK") and ",-5," in cells and "'+91 99999" in cells and cells.endswith(",plain")


def test_the_refund_report_shows_turnaround_and_open_ones(world):
    a, b, c = setup_sales(world)
    open_one = world.as_user(world.people.ravi).post("/refunds", json={"payment_id": b["id"], "amount": "5", "reason": "Please"}).json()

    report = world.as_user(world.people.asha).get(f"{REPORTS}/refunds").json()

    assert report["totals"]["refunds"] == 2 and report["totals"]["completed"] == 1 and report["totals"]["open"] == 1 and report["totals"]["base_refunded"] == "590.00"
    done = next(r for r in report["rows"] if r["status"] == "completed")
    assert done["amount"] == "590.00" and done["tax_refunded"] == "90.00" and done["turnaround_hours"] is not None and done["destination"] == "original"
    assert next(r for r in report["rows"] if r["status"] == "requested")["refund_id"] == open_one["id"]
    assert world.as_user(world.people.root).get(f"{REPORTS}/refunds", params={"format": "csv"}).text.splitlines()[0].startswith("refund_id,payment_id")


def test_the_settlement_report_totals_by_currency(world):
    setup_sales(world)
    world.as_user(world.people.root).post("/settlements/run", json={"tenant_id": str(TENANT_A)})

    report = world.as_user(world.people.asha).get(f"{REPORTS}/settlements").json()

    assert report["totals"]["settlements"] == 1 and list(report["totals"]["by_currency"]) == ["INR"]
    row = report["rows"][0]
    assert row["status"] == "pending" and Decimal(row["net_amount"]) == Decimal(report["totals"]["by_currency"]["INR"]["pending"])
    assert world.as_user(world.people.bela).get(f"{REPORTS}/settlements").json()["total_rows"] == 0


def test_the_currency_report_groups_by_what_customers_paid_in(world):
    setup_sales(world)

    report = world.as_user(world.people.root).get(f"{REPORTS}/currency").json()

    by_code = {r["currency"]: r for r in report["rows"]}
    assert set(by_code) == {"INR", "USD"} and by_code["INR"]["payments"] == 2 and by_code["USD"]["payments"] == 1
    assert by_code["USD"]["charge_total"] == "28.32" and by_code["USD"]["base_total"] == "2360.00" and by_code["INR"]["base_total"] == "1770.00"
    assert report["totals"] == {"payments": 3, "base_total": "4130.00"}
    assert world.as_user(world.people.asha).get(f"{REPORTS}/currency").json()["totals"]["payments"] == 2


def test_the_exchange_rate_report_has_history_and_low_high_average(world):
    root = world.people.root
    world.as_user(root).post("/currencies/rates", json={"quote_currency": "USD", "rate": "0.0130"})
    world.as_user(root).post("/currencies/rates", json={"quote_currency": "USD", "rate": "0.0110"})

    report = world.as_user(root).get(f"{REPORTS}/exchange-rates", params={"quote": "usd"}).json()

    usd = report["summary"][0]
    assert usd["currency"] == "USD" and usd["changes"] == 3 and usd["low"] == "0.0110000000" and usd["high"] == "0.0130000000" and usd["average"] == "0.0120000000"
    assert [r["rate"] for r in report["rows"]][0] in ("0.0110000000", "0.0130000000", "0.0120000000")
    assert world.as_user(root).get(f"{REPORTS}/exchange-rates", params={"format": "csv"}).text.startswith("quote_currency,base_currency,rate")


# --- tax history and report from real payments ----------------------------------------------------------------------------------------------

def test_tax_history_and_report_come_from_paid_payments_net_of_refunds(world):
    a, b, c = setup_sales(world)

    history = world.as_user(world.people.asha).get("/tax/history").json()
    report = world.as_user(world.people.asha).get("/tax/reports", params={"group_by": "name"}).json()

    assert history["total"] == 6                                                        # business A: 2 payments x 2 components, and the 2 rows of the refund
    names = {r["name"]: r for r in report["rows"]}
    assert set(names) == {"CGST", "SGST"}
    assert names["CGST"]["transactions"] == 3                                            # 2 charges and 1 reversal
    assert names["CGST"]["tax_amount"] == {"INR": "225.00"} and names["CGST"]["taxable_amount"] == {"INR": "2500.00"}      # 1000 + 2000 charged, 500 given back; tax 90 + 180 - 45
    assert report["total_tax_base"] == "450.00"                                          # 540 charged less 90 given back
    mine = world.as_user(world.people.priya).get("/tax/history").json()
    assert mine["total"] == 4                                                           # a customer sees the tax of their own payments
    assert world.as_user(world.people.root).get("/tax/history", params={"tenant_id": str(TENANT_B)}).json()["total"] == 1       # business B has no tax profile, so one IGST line
    assert world.as_user(world.people.priya).get("/tax/reports").status_code == 403
    assert world.as_user(world.people.asha).get("/tax/reports", params={"format": "csv"}).text.splitlines()[0].startswith("tax_type,transactions")


# --- reconciliation with the gateway's report ----------------------------------------------------------------------------------------------

def row_for(world, payment, **overrides):
    p = world.db.get(Payment, UUID(payment["id"]))
    base = {"gateway_ref": p.gateway_payment_id, "type": "payment", "amount": str(p.charge_total), "currency": p.charge_currency, "status": "captured",
            "fee": str(p.gateway_fee) if p.gateway_fee is not None else None}
    base.update(overrides)
    return base


def reconcile(world, rows, **extra):
    resp = world.as_user(world.people.root).post("/finance/reconciliation", json={"rows": rows, **extra})
    assert resp.status_code == 200 or resp.status_code == 201, resp.text
    return resp.json()


def test_a_matching_gateway_report_has_no_mismatches(world):
    a, b, c = setup_sales(world)

    run = reconcile(world, [row_for(world, a), row_for(world, b), row_for(world, c)])

    assert run["total_rows"] == 3 and run["mismatched"] == 0 and run["matched"] == 3 and run["items"] == [] and run["provider"] == "test"


def test_every_kind_of_difference_is_found(world):
    a, b, c = setup_sales(world)
    pa = world.db.get(Payment, UUID(a["id"]))
    rows = [
        row_for(world, a, amount="1000.00"),                                                       # amount differs
        row_for(world, b, status="failed"),                                                        # we say paid
        row_for(world, c, fee="999.00"),                                                           # fee differs
        {"gateway_ref": "test_pay_unknown", "type": "payment", "amount": "50", "currency": "INR", "status": "captured"},   # money we do not know
        row_for(world, c),                                                                         # the same id twice
        row_for(world, a, currency="EUR", gateway_ref=pa.gateway_payment_id) | {"type": "payment"},
    ]

    run = reconcile(world, rows)

    issues = sorted(i["issue"] for i in run["items"])
    assert issues == sorted(["amount_mismatch", "status_mismatch", "fee_mismatch", "missing_internal", "duplicate", "duplicate"])
    unknown = next(i for i in run["items"] if i["issue"] == "missing_internal")
    assert unknown["gateway_ref"] == "test_pay_unknown" and unknown["gateway_amount"] == "50.0000"
    amount = next(i for i in run["items"] if i["issue"] == "amount_mismatch")
    assert amount["gateway_amount"] == "1000.0000" and amount["internal_amount"] == "1180.0000" and amount["internal_status"] == "partially_refunded"


def test_a_payment_the_gateway_report_lacks_is_flagged_for_the_period(world):
    a, b, c = setup_sales(world)
    start, end = (datetime.utcnow() - timedelta(hours=1)).isoformat(), (datetime.utcnow() + timedelta(hours=1)).isoformat()

    run = reconcile(world, [row_for(world, a), row_for(world, c)], period_start=start, period_end=end)

    [item] = run["items"]
    assert item["issue"] == "missing_gateway" and item["payment_id"] == b["id"] and item["internal_status"] == "succeeded"


def test_refund_rows_are_matched_by_the_gateway_refund_id(world):
    a, b, c = setup_sales(world)
    refund_id = world.db.query(__import__("app.models.payment_model", fromlist=["Refund"]).Refund).first().gateway_refund_id

    ok = reconcile(world, [{"gateway_ref": refund_id, "type": "refund", "amount": "590.00", "currency": "INR"}])
    bad = reconcile(world, [{"gateway_ref": refund_id, "type": "refund", "amount": "500.00", "currency": "INR"},
                            {"gateway_ref": "nope", "type": "refund", "amount": "1", "currency": "INR"}])

    assert ok["mismatched"] == 0
    assert sorted(i["issue"] for i in bad["items"]) == ["amount_mismatch", "missing_internal"]


def test_mismatches_are_listed_resolved_and_reported(world):
    a, b, c = setup_sales(world)
    run = reconcile(world, [row_for(world, a, amount="1.00"), {"gateway_ref": "x", "amount": "1", "currency": "INR", "status": "captured"}])
    root = world.people.root

    runs = world.as_user(root).get("/finance/reconciliation").json()
    detail = world.as_user(root).get(f"/finance/reconciliation/{run['id']}", params={"issue": "missing_internal"}).json()
    assert runs["total"] == 1 and [i["issue"] for i in detail["items"]] == ["missing_internal"]
    item = detail["items"][0]
    assert world.as_user(root).post(f"/finance/reconciliation/items/{item['id']}/resolve", json={"note": "x"}).status_code == 422
    done = world.as_user(root).post(f"/finance/reconciliation/items/{item['id']}/resolve", json={"note": "Refunded on the dashboard"}).json()
    assert done["resolved"] is True and done["resolved_by"] == root["id"] and done["note"] == "Refunded on the dashboard"
    assert world.as_user(root).post(f"/finance/reconciliation/items/{item['id']}/resolve", json={"note": "Again please"}).status_code == 409
    assert len(world.as_user(root).get(f"/finance/reconciliation/{run['id']}", params={"resolved": "false"}).json()["items"]) == 1

    report = world.as_user(root).get(f"{REPORTS}/reconciliation").json()
    assert report["totals"] == {"runs": 1, "items": 2, "open": 1}
    assert {b["issue"]: b["open"] for b in report["by_issue"]} == {"amount_mismatch": 1, "missing_internal": 0}


def test_reconciliation_is_for_the_platform_only(world):
    for user in (world.people.asha, world.people.priya):
        assert world.as_user(user).post("/finance/reconciliation", json={"rows": [{"gateway_ref": "x"}]}).status_code == 403
        assert world.as_user(user).get("/finance/reconciliation").status_code == 403
    assert world.as_user(world.people.root).post("/finance/reconciliation", json={"rows": []}).status_code == 422


# --- the audit log -----------------------------------------------------------------------------------------------------------------------------------

def test_the_audit_log_is_for_the_platform_and_can_be_filtered(world):
    root = world.people.root
    world.as_user(root).post("/currencies/rates", json={"quote_currency": "USD", "rate": "0.0125", "note": "Month end"})
    world.as_user(root).post("/tax/rules", json={"tax_type": "gst", "name": "GST", "country": "IN", "rate": "18"})
    world.as_user(world.people.asha).put("/settlements/config", json={"schedule": "daily"})

    everything = world.as_user(root).get("/finance/audit").json()
    rates = world.as_user(root).get("/finance/audit", params={"entity_type": "exchange_rate"}).json()
    mine = world.as_user(root).get("/finance/audit", params={"actor_id": world.people.asha["id"]}).json()

    assert everything["total"] == 3 and rates["total"] == 1 and rates["items"][0]["action"] == "exchange_rate_set" and rates["items"][0]["reason"] == "Month end"
    assert rates["items"][0]["actor_name"] == "Platform Root" and rates["items"][0]["actor_role"] == "super_admin"
    assert mine["total"] == 1 and mine["items"][0]["entity_type"] == "settlement_config" and mine["items"][0]["tenant_id"] == str(TENANT_A)
    assert world.as_user(root).get("/finance/audit", params={"tenant_id": str(TENANT_A)}).json()["total"] == 1
    for user in (world.people.asha, world.people.priya):
        assert world.as_user(user).get("/finance/audit").status_code == 403
    assert world.as_user(None).get("/finance/audit").status_code == 401


def test_the_audit_log_cannot_be_changed_through_the_api(world):
    paths = [("put", "/finance/audit"), ("delete", "/finance/audit"), ("post", "/finance/audit"), ("patch", "/finance/audit")]
    for method, path in paths:
        assert getattr(world.as_user(world.people.root), method)(path).status_code == 405, (method, path)


# --- the jobs ----------------------------------------------------------------------------------------------------------------------------------------------

def test_the_jobs_run_by_hand_and_report_what_they_did(world, monkeypatch):
    setup_sales(world)
    monkeypatch.setattr(currency_service, "fetch_provider_rates", lambda base, quotes: {"USD": Decimal("0.0125")})

    out = world.as_user(world.people.root).post("/finance/jobs/run", json={}).json()

    assert set(out) == {"rates", "payments", "refunds", "settlements", "reminders"}
    assert out["rates"]["updated"] == ["USD"] and out["rates"]["missing"] == ["EUR", "JPY", "KWD"]
    assert out["payments"] == {"checked": 0, "paid": 0, "failed": 0, "expired": 0, "review": 0, "unreachable": 0}
    assert out["refunds"] == {"retried": 0, "escalated": 0} and out["reminders"] == {"sent": 0}
    assert out["settlements"]["created"] == 2 and out["settlements"]["failed"] == 0
    only = world.as_user(world.people.root).post("/finance/jobs/run", json={"jobs": ["reminders"]}).json()
    assert list(only) == ["reminders"]
    assert world.as_user(world.people.root).post("/finance/jobs/run", json={"jobs": ["bake"]}).status_code == 422
    assert world.as_user(world.people.asha).post("/finance/jobs/run", json={}).status_code == 403


def test_one_failing_job_does_not_stop_the_others(world, monkeypatch):
    def down(base, quotes):
        raise TimeoutError("no answer")

    monkeypatch.setattr(currency_service, "fetch_provider_rates", down)

    out = world.as_user(world.people.root).post("/finance/jobs/run", json={"jobs": ["rates", "reminders"]}).json()

    assert "error" in out["rates"] and out["reminders"] == {"sent": 0}


# --- the export for the accounts --------------------------------------------------------------------------------------------------------------------

def test_the_accounting_export_has_payments_refunds_invoices_and_tax(world):
    setup_sales(world)
    asha = world.people.asha

    payments = asha_get(world, "payments")
    assert payments["count"] == 2 and payments["rows"][0]["base_currency"] == "INR"
    assert asha_get(world, "refunds")["count"] == 1
    invoices = asha_get(world, "invoices")
    assert invoices["count"] == 3 and sorted(r["doc_type"] for r in invoices["rows"]) == ["credit_note", "invoice", "invoice"]
    assert asha_get(world, "tax")["count"] == 6
    csv = world.as_user(asha).get("/finance/export", params={"kind": "invoices"})
    assert csv.headers["content-type"].startswith("text/csv") and csv.text.splitlines()[0].startswith("number,doc_type,issued_at")
    assert world.as_user(world.people.root).get("/finance/export", params={"kind": "payments", "format": "json"}).json()["count"] == 3
    assert world.as_user(asha).get("/finance/export", params={"kind": "payments", "tenant_id": str(TENANT_B)}).status_code == 403
    assert world.as_user(world.people.priya).get("/finance/export", params={"kind": "payments"}).status_code == 403
    assert world.as_user(asha).get("/finance/export", params={"kind": "secrets"}).status_code == 422


def asha_get(world, kind):
    return world.as_user(world.people.asha).get("/finance/export", params={"kind": kind, "format": "json"}).json()
