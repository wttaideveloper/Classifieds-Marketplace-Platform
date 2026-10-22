"""Merchant settlement: schedule and currency, commission and fee deduction, itemised payouts, refunds, the 1:1 check against the
underlying transactions, and who can do what."""
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.models.payment_model import FinanceAuditLog, MerchantSettlement, Payment, Refund, SettlementConfig, SettlementItem
from app.services import settlement_service
from tests.pay_support import TENANT_A, TENANT_B, cart_order, india_gst, notices_of, pay, world  # noqa: F401

API = "/settlements"
ROOT_RUN = f"{API}/run"


def sale(world, user=None, total="1000", tenant=TENANT_A, **extra):
    """A paid sale: total + 18% GST (the pre-tax part is `total`)."""
    user = user or world.people.priya
    return pay(world, user, cart_order(world, user, total=total, tenant=tenant), **extra).json()


def run(world, tenant=TENANT_A, **body):
    resp = world.as_user(world.people.root).post(ROOT_RUN, json={"tenant_id": str(tenant), **body})
    assert resp.status_code == 200, resp.text
    return resp.json()


def setup(world):
    india_gst(world)


# --- configuration ------------------------------------------------------------------------------------------------------------------------------------

def test_a_business_has_a_default_schedule_and_can_change_its_own_settings(world):
    asha = world.people.asha

    default = world.as_user(asha).get(f"{API}/config").json()
    assert default["schedule"] == "weekly" and default["currency"] == "INR" and default["effective_commission_percent"] == "10" and default["hold"] is False

    saved = world.as_user(asha).put(f"{API}/config", json={"schedule": "monthly", "month_day": 5, "currency": "usd", "min_payout": "25",
                                                           "payout_account": {"bank_name": "HDFC Bank", "account_last4": "4321", "holder": "Calm Clinic"}}).json()
    assert saved["schedule"] == "monthly" and saved["month_day"] == 5 and saved["currency"] == "USD" and saved["min_payout"] == "25.00"
    assert saved["payout_account"]["account_last4"] == "4321"
    entry = world.db.query(FinanceAuditLog).filter_by(action="settlement_config_saved").one()
    assert entry.actor_id == asha["id"] and entry.before["currency"] == "INR" and entry.after["currency"] == "USD"


def test_only_the_platform_sets_commission_fees_and_holds(world):
    asha, root = world.people.asha, world.people.root

    for body in ({"commission_percent": "1"}, {"fixed_fee": "5"}, {"hold": True}):
        assert world.as_user(asha).put(f"{API}/config", json=body).status_code == 403, body
    done = world.as_user(root).put(f"{API}/config", params={"tenant_id": str(TENANT_A)}, json={"commission_percent": "7.5", "fixed_fee": "2", "hold": False}).json()
    assert done["commission_percent"] == "7.5" and done["effective_commission_percent"] == "7.5" and done["fixed_fee"] == "2.00"
    assert world.as_user(asha).get(f"{API}/config").json()["commission_percent"] == "7.5"                      # the business can read what it is charged
    assert world.as_user(root).put(f"{API}/config", json={"hold": True}).status_code == 422                          # which business?
    assert world.as_user(world.people.bela).get(f"{API}/config", params={"tenant_id": str(TENANT_A)}).status_code == 403
    assert world.as_user(world.people.priya).get(f"{API}/config").status_code == 403


def test_settings_are_validated(world):
    asha = world.people.asha

    assert world.as_user(asha).put(f"{API}/config", json={"currency": "GBP"}).status_code == 422                     # switched off
    assert world.as_user(asha).put(f"{API}/config", json={"schedule": "hourly"}).status_code == 422
    assert world.as_user(asha).put(f"{API}/config", json={"month_day": 31}).status_code == 422
    assert world.as_user(asha).put(f"{API}/config", json={"payout_account": {"account_number": "123456789012"}}).status_code == 422
    assert world.as_user(asha).put(f"{API}/config", json={"payout_account": {"ifsc": "HDFC0001234", "account_last4": "4321"}}).status_code == 200


# --- the amounts -------------------------------------------------------------------------------------------------------------------------------------

def test_a_settlement_takes_commission_and_fees_and_lists_every_item(world):
    setup(world)
    first, second = sale(world), sale(world, world.people.ravi, total="2000")

    out = run(world)["settlement"]

    assert out["status"] == "pending" and out["currency"] == "INR" and out["items_count"] == 2 and out["reference"].startswith("SET-")
    # gross 1180 + 2360; commission 10% of the pre-tax 1000 + 2000; gateway fee 2% of the gross (the test gateway reports it)
    assert out["gross_amount"] == "3540.00" and out["commission_amount"] == "300.00" and out["fee_amount"] == "70.80" and out["fixed_fee_amount"] == "0.00"
    assert out["net_amount"] == "3169.20" and out["tax_collected"] == "540.00" and out["refund_amount"] == "0.00"
    detail = world.as_user(world.people.asha).get(f"{API}/{out['id']}").json()
    assert [i["kind"] for i in detail["items"]] == ["payment", "payment"]
    by_payment = {i["payment_id"]: i for i in detail["items"]}
    one = by_payment[first["id"]]
    assert (one["gross"], one["commission"], one["fee"], one["net"]) == ("1180.00", "100.00", "23.60", "1056.40")
    assert by_payment[second["id"]]["net"] == "2112.80"
    for payment in (first, second):
        assert str(world.db.get(Payment, UUID(payment["id"])).settlement_id) == out["id"]
    assert sum(Decimal(i["net"]) for i in detail["items"]) == Decimal(out["net_amount"])


def test_the_business_commission_and_a_fixed_fee_override_the_defaults(world):
    setup(world)
    sale(world)
    world.as_user(world.people.root).put(f"{API}/config", params={"tenant_id": str(TENANT_A)}, json={"commission_percent": "5", "fixed_fee": "10"})

    out = run(world)["settlement"]

    assert out["commission_amount"] == "50.00" and out["fixed_fee_amount"] == "10.00" and out["net_amount"] == "1096.40"


def test_an_estimated_gateway_fee_is_used_when_the_gateway_did_not_report_one(world):
    setup(world)
    payment = sale(world)
    world.db.get(Payment, UUID(payment["id"])).gateway_fee = None
    world.db.commit()

    out = run(world)["settlement"]

    assert out["fee_amount"] == "23.60"                                                                               # 2% of the gross


def test_the_payout_currency_is_converted_with_the_rate_of_the_day(world):
    setup(world)
    sale(world, currency="USD")                                                                                       # 14.16 USD
    world.as_user(world.people.asha).put(f"{API}/config", json={"currency": "EUR"})

    out = run(world)["settlement"]

    # 14.16 USD -> EUR at 0.011 / 0.012; commission is 10% of the pre-tax 12 USD
    assert out["currency"] == "EUR" and out["exchange_rate"] == "0.0110000000" and out["base_currency"] == "INR"
    assert out["gross_amount"] == "12.98" and out["commission_amount"] == "1.10"
    item = world.as_user(world.people.asha).get(f"{API}/{out['id']}").json()["items"][0]
    assert item["charge_currency"] == "USD" and item["charge_amount"] == "14.16" and item["gross"] == "12.98"


def test_a_refund_after_a_settlement_comes_off_the_next_one(world):
    setup(world)
    payment = sale(world)
    sale(world, world.people.ravi, total="2000")
    first = run(world)["settlement"]
    world.as_user(world.people.asha).post("/refunds", json={"payment_id": payment["id"], "amount": "590"})

    assert run(world)["settlement"] is None                                                                          # a refund alone is negative, so it waits
    assert world.db.query(MerchantSettlement).count() == 1
    sale(world, world.people.ravi, total="3000")
    third = run(world)["settlement"]

    items = world.as_user(world.people.asha).get(f"{API}/{third['id']}").json()["items"]
    assert sorted(i["kind"] for i in items) == ["payment", "refund"]
    refund = next(i for i in items if i["kind"] == "refund")
    assert refund["gross"] == "-590.00" and refund["commission"] == "-50.00" and refund["net"] == "-540.00"        # the commission on the pre-tax 500 comes back
    assert third["refund_amount"] == "590.00" and third["id"] != first["id"]
    # the new sale: 3540 gross - 300 commission - 70.80 fee = 3169.20; less the refund's net of 540
    assert (third["gross_amount"], third["commission_amount"], third["fee_amount"], third["net_amount"]) == ("3540.00", "250.00", "70.80", "2629.20")
    assert world.as_user(world.people.asha).get(f"{API}/{third['id']}/verify").json()["reconciled"] is True


def test_a_payment_is_settled_only_once(world):
    setup(world)
    sale(world)

    first = run(world)["settlement"]
    second = run(world)

    assert first is not None and second["settlement"] is None
    assert world.db.query(MerchantSettlement).count() == 1 and world.db.query(SettlementItem).count() == 1


def test_a_payment_that_is_not_paid_is_not_settled(world):
    setup(world)
    pay(world, world.people.priya, cart_order(world, world.people.priya), token="tok_fail")
    pay(world, world.people.priya, cart_order(world, world.people.priya), token=None)

    assert run(world)["settlement"] is None


def test_businesses_are_settled_separately(world):
    setup(world)
    sale(world)
    sale(world, world.people.ravi, tenant=TENANT_B)

    mine = run(world, TENANT_A)["settlement"]
    theirs = run(world, TENANT_B)["settlement"]

    assert mine["tenant_id"] == str(TENANT_A) and theirs["tenant_id"] == str(TENANT_B) and mine["items_count"] == theirs["items_count"] == 1
    assert world.as_user(world.people.asha).get(API).json()["total"] == 1
    assert world.as_user(world.people.bela).get(f"{API}/{mine['id']}").status_code == 404


# --- not yet / not at all -------------------------------------------------------------------------------------------------------------------------------------

def test_below_the_minimum_payout_or_on_hold_nothing_is_settled(world):
    setup(world)
    sale(world)
    world.as_user(world.people.asha).put(f"{API}/config", json={"min_payout": "5000"})

    assert run(world)["settlement"] is None
    forced = run(world, force=True)["settlement"]
    assert forced is not None and forced["net_amount"] == "1056.40"

    sale(world, world.people.ravi)
    world.as_user(world.people.root).put(f"{API}/config", params={"tenant_id": str(TENANT_A)}, json={"hold": True, "min_payout": "0"})
    assert run(world)["settlement"] is None
    assert run(world, force=True)["settlement"] is not None


def test_a_dry_run_shows_the_settlement_without_saving_it(world):
    setup(world)
    payment = sale(world)

    preview = run(world, dry_run=True)

    assert preview["message"].startswith("Dry run")
    shown = preview["settlement"]
    assert (shown["gross_amount"], shown["commission_amount"], shown["fee_amount"], shown["net_amount"], shown["items_count"]) == ("1180.00", "100.00", "23.60", "1056.40", 1)
    assert world.db.query(MerchantSettlement).count() == 0
    world.db.expire_all()
    assert world.db.get(Payment, UUID(payment["id"])).settlement_id is None
    assert run(world, tenant=TENANT_B, dry_run=True)["settlement"] is None


def test_only_the_platform_runs_settlements(world):
    for user in (world.people.asha, world.people.priya):
        assert world.as_user(user).post(ROOT_RUN, json={}).status_code == 403
    assert world.as_user(world.people.root).post(ROOT_RUN, json={"force": True}).status_code == 422                  # force needs a business


# --- paying out ----------------------------------------------------------------------------------------------------------------------------------------------------

def test_finance_records_the_payout_and_the_business_is_told(world):
    setup(world)
    sale(world)
    out = run(world)["settlement"]
    root, asha = world.people.root, world.people.asha

    assert world.as_user(asha).post(f"{API}/{out['id']}/pay", json={"payout_reference": "UTR123456"}).status_code == 403
    paid = world.as_user(root).post(f"{API}/{out['id']}/pay", json={"payout_reference": "UTR123456"}).json()

    assert paid["status"] == "paid" and paid["payout_reference"] == "UTR123456" and paid["paid_at"] and paid["actions"] == ["verify"]
    [notice] = notices_of(world, "settlement_completed")
    assert str(notice["user_ids"][0]) == asha["id"] and notice["metadata"]["payout_reference"] == "UTR123456" and "INR 1,056.40" in notice["message"]
    assert world.as_user(root).post(f"{API}/{out['id']}/pay", json={"payout_reference": "UTR2"}).status_code == 409
    assert world.as_user(root).post(f"{API}/{out['id']}/cancel", json={"reason": "Too late"}).status_code == 409


def test_a_failed_payout_can_be_cancelled_and_its_items_join_the_next_settlement(world):
    setup(world)
    payment = sale(world)
    out = run(world)["settlement"]
    root = world.people.root

    failed = world.as_user(root).post(f"{API}/{out['id']}/fail", json={"reason": "Bank rejected the transfer"}).json()
    assert failed["status"] == "failed" and failed["failure_reason"] == "Bank rejected the transfer" and failed["actions"] == ["cancel", "verify"]
    cancelled = world.as_user(root).post(f"{API}/{out['id']}/cancel", json={"reason": "Wrong account"}).json()

    assert cancelled["status"] == "cancelled"
    assert world.db.get(Payment, UUID(payment["id"])).settlement_id is None
    again = run(world)["settlement"]
    assert again["id"] != out["id"] and again["net_amount"] == "1056.40" and again["reference"] != out["reference"]


# --- the 1:1 check ---------------------------------------------------------------------------------------------------------------------------------------------------

def test_a_settlement_reconciles_one_to_one_with_its_payments_and_refunds(world):
    setup(world)
    payment = sale(world)
    sale(world, world.people.ravi, total="2000")
    world.as_user(world.people.asha).post("/refunds", json={"payment_id": payment["id"], "amount": "100"})
    out = run(world)["settlement"]

    check = world.as_user(world.people.asha).get(f"{API}/{out['id']}/verify").json()

    assert check["reconciled"] is True and check["problems"] == [] and check["items_checked"] == 3 and check["payments"] == 2 and check["refunds"] == 1


def test_the_check_notices_when_a_transaction_or_a_total_has_changed(world):
    setup(world)
    first = sale(world)
    sale(world, world.people.ravi)
    out = run(world)["settlement"]
    row = world.db.get(MerchantSettlement, UUID(out["id"]))
    world.db.get(Payment, UUID(first["id"])).charge_total = Decimal("1.00")                                            # a payment changed behind the settlement's back
    row.net_amount = row.net_amount + Decimal("5")
    world.db.add(Refund(payment_id=UUID(first["id"]), tenant_id=TENANT_A, amount=Decimal("1"), currency="INR", refund_type="partial", status="completed",
                        settlement_id=row.id, completed_at=datetime.utcnow(), destination="original"))
    world.db.commit()

    problems = {p["issue"] for p in world.as_user(world.people.root).get(f"{API}/{out['id']}/verify").json()["problems"]}

    assert {"amount_changed", "total_mismatch", "refund_without_item"} <= problems
    assert world.as_user(world.people.root).get(f"{API}/{out['id']}/verify").json()["reconciled"] is False


def test_the_csv_export_has_one_line_per_item(world):
    setup(world)
    sale(world)
    sale(world, world.people.ravi)
    out = run(world)["settlement"]

    resp = world.as_user(world.people.asha).get(f"{API}/{out['id']}/export")

    lines = resp.text.strip().splitlines()
    assert resp.headers["content-type"].startswith("text/csv") and resp.headers["content-disposition"] == f'attachment; filename="{out["reference"]}.csv"'
    assert lines[0].startswith("reference,currency,kind") and len(lines) == 3 and "1056.4000" in resp.text


# --- the schedule ------------------------------------------------------------------------------------------------------------------------------------------------------

def config(**values):
    base = dict(tenant_id=TENANT_A, schedule="weekly", weekday=0, month_day=1, currency="INR", hold=False, last_run_at=None)
    base.update(values)
    return SettlementConfig(**base)


def test_the_schedule_decides_when_a_business_is_due():
    monday = datetime(2026, 10, 19, 6, 0)           # a Monday
    tuesday = monday + timedelta(days=1)

    assert settlement_service.is_due(config(schedule="daily"), monday)
    assert not settlement_service.is_due(config(schedule="daily", last_run_at=monday - timedelta(hours=2)), monday)             # already today
    assert settlement_service.is_due(config(schedule="daily", last_run_at=monday - timedelta(days=1)), monday)
    assert settlement_service.is_due(config(schedule="weekly", weekday=0, last_run_at=monday - timedelta(days=7)), monday)
    assert not settlement_service.is_due(config(schedule="weekly", weekday=0, last_run_at=monday - timedelta(days=1)), tuesday)
    assert settlement_service.is_due(config(schedule="weekly", weekday=0, last_run_at=monday - timedelta(days=9)), tuesday)    # missed Monday
    assert settlement_service.is_due(config(schedule="monthly", month_day=5, last_run_at=datetime(2026, 9, 5)), datetime(2026, 10, 5, 6))
    assert not settlement_service.is_due(config(schedule="monthly", month_day=5, last_run_at=datetime(2026, 10, 5)), datetime(2026, 10, 20))
    assert not settlement_service.is_due(config(schedule="monthly", month_day=5, last_run_at=datetime(2026, 9, 5)), datetime(2026, 10, 3))
    assert settlement_service.is_due(config(schedule="monthly", month_day=5, last_run_at=datetime(2026, 8, 5)), datetime(2026, 10, 9))     # missed September
    assert settlement_service.is_due(config(schedule="monthly", month_day=31), datetime(2026, 10, 28))                                    # capped to the 28th
    assert not settlement_service.is_due(config(hold=True), monday)


def test_the_scheduled_job_settles_the_businesses_that_are_due(world):
    setup(world)
    sale(world)
    sale(world, world.people.ravi, tenant=TENANT_B)
    world.as_user(world.people.asha).put(f"{API}/config", json={"schedule": "daily"})
    world.as_user(world.people.bela).put(f"{API}/config", json={"schedule": "monthly", "month_day": 28})
    world.db.query(SettlementConfig).filter_by(tenant_id=TENANT_B).update({"last_run_at": datetime.utcnow().replace(day=1, hour=0, minute=0)})
    world.db.commit()

    now = datetime.utcnow() + timedelta(minutes=1)
    summary = settlement_service.run_due(world.db, now)

    assert summary == {"businesses": 2, "created": 1, "skipped": 1, "failed": 0}
    assert world.db.query(MerchantSettlement).one().tenant_id == TENANT_A
    again = settlement_service.run_due(world.db, now + timedelta(hours=1))
    assert again["created"] == 0                                                                                       # nothing new, and not twice in a day


def test_the_job_gives_a_business_without_a_schedule_the_default_one(world):
    setup(world)
    sale(world)

    summary = settlement_service.run_due(world.db, datetime.utcnow() + timedelta(minutes=1))

    assert summary["created"] == 1 and world.db.get(SettlementConfig, TENANT_A).schedule == "weekly"


def test_the_history_is_scoped_and_filterable(world):
    setup(world)
    sale(world)
    sale(world, world.people.ravi, tenant=TENANT_B)
    first = run(world, TENANT_A)["settlement"]
    run(world, TENANT_B)
    world.as_user(world.people.root).post(f"{API}/{first['id']}/pay", json={"payout_reference": "UTR1"})

    assert world.as_user(world.people.asha).get(API).json()["total"] == 1
    assert world.as_user(world.people.root).get(API).json()["total"] == 2
    assert world.as_user(world.people.root).get(API, params={"status": "paid"}).json()["total"] == 1
    assert world.as_user(world.people.root).get(API, params={"tenant_id": str(TENANT_B)}).json()["total"] == 1
    assert world.as_user(world.people.priya).get(API).status_code == 403
    assert world.as_user(None).get(API).status_code == 401
