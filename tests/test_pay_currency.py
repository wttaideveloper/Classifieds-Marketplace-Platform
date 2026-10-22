"""Supported currencies, exchange rates, conversion and the preferred currency."""
from datetime import datetime, timedelta
from decimal import Decimal

from app.models.payment_model import ExchangeRate, FinanceAuditLog
from app.services import currency_service
from tests.pay_support import world  # noqa: F401

API = "/currencies"


def test_the_public_list_has_the_active_currencies_with_their_decimals(world):
    body = world.as_user(None).get(API).json()

    assert body["base_currency"] == "INR" and body["total"] == 5
    by_code = {c["code"]: c for c in body["items"]}
    assert "GBP" not in by_code                                       # switched off
    assert by_code["JPY"]["decimals"] == 0 and by_code["KWD"]["decimals"] == 3 and by_code["USD"]["decimals"] == 2
    assert by_code["INR"]["is_base"] is True and by_code["USD"]["is_base"] is False


def test_the_super_admin_adds_changes_and_switches_off_currencies(world):
    root = world.people.root
    added = world.as_user(root).post(API, json={"code": "aud", "min_amount": "1", "max_amount": "5000"})

    assert added.status_code == 201 and added.json()["code"] == "AUD" and added.json()["name"] == "Australian Dollar"
    assert added.json()["min_amount"] == "1.0000" and added.json()["decimals"] == 2
    assert world.as_user(root).post(API, json={"code": "AUD"}).status_code == 409
    assert world.as_user(root).post(API, json={"code": "QQQ"}).status_code == 422                      # unknown code, no name
    assert world.as_user(root).post(API, json={"code": "XYZ", "name": "Made-up money", "decimals": 5}).status_code == 422
    changed = world.as_user(root).patch(f"{API}/AUD", json={"is_active": False, "name": "Aussie dollar"}).json()
    assert changed["is_active"] is False and changed["name"] == "Aussie dollar"
    assert world.as_user(root).patch(f"{API}/AUD", json={"clear_limits": True}).json()["min_amount"] is None
    assert world.as_user(root).patch(f"{API}/AUD", json={"min_amount": "10", "max_amount": "5"}).status_code == 422
    assert world.as_user(root).patch(f"{API}/INR", json={"is_active": False}).status_code == 409        # the base stays on
    assert "AUD" in {c["code"] for c in world.as_user(root).get(f"{API}/admin/all").json()["items"]}
    assert "AUD" not in {c["code"] for c in world.as_user(None).get(API).json()["items"]}
    assert world.db.query(FinanceAuditLog).filter_by(action="currency_updated").count() == 2


def test_only_the_super_admin_manages_currencies_and_rates(world):
    for user in (world.people.asha, world.people.priya):
        assert world.as_user(user).post(API, json={"code": "AUD"}).status_code == 403
        assert world.as_user(user).patch(f"{API}/USD", json={"is_active": False}).status_code == 403
        assert world.as_user(user).post(f"{API}/rates", json={"quote_currency": "USD", "rate": "0.01"}).status_code == 403
        assert world.as_user(user).post(f"{API}/rates/refresh").status_code == 403
        assert world.as_user(user).get(f"{API}/rates/history").status_code == 403
    assert world.as_user(None).post(API, json={"code": "AUD"}).status_code == 401


def test_conversion_goes_through_the_base_currency_and_rounds_to_the_target(world):
    one = world.as_user(None).get(f"{API}/convert", params={"amount": "1000", "from": "INR", "to": "USD"}).json()
    assert one["rate"] == "0.0120000000" and one["converted"] == "12.00"

    cross = world.as_user(None).get(f"{API}/convert", params={"amount": "100", "from": "USD", "to": "EUR"}).json()
    assert Decimal(cross["rate"]) == Decimal("0.9166666667") and cross["converted"] == "91.67"

    yen = world.as_user(None).get(f"{API}/convert", params={"amount": "1000", "from": "INR", "to": "JPY"}).json()
    assert yen["converted"] == "1800"                                                           # no decimals
    kwd = world.as_user(None).get(f"{API}/convert", params={"amount": "1000", "from": "INR", "to": "KWD"}).json()
    assert kwd["converted"] == "3.700"                                                          # three decimals
    same = world.as_user(None).get(f"{API}/convert", params={"amount": "5.5", "from": "USD", "to": "USD"}).json()
    assert same["rate"] == "1" and same["converted"] == "5.50"


def test_conversion_refuses_unsupported_currencies(world):
    assert world.as_user(None).get(f"{API}/convert", params={"amount": "1", "from": "INR", "to": "GBP"}).status_code == 422
    assert world.as_user(None).get(f"{API}/convert", params={"amount": "1", "from": "INR", "to": "ZZZ"}).status_code == 422
    assert world.as_user(None).get(f"{API}/convert", params={"amount": "-1", "from": "INR", "to": "USD"}).status_code == 422


def test_the_rate_list_shows_the_newest_rate_of_each_active_currency(world):
    body = world.as_user(None).get(f"{API}/rates").json()

    assert body["base_currency"] == "INR"
    assert {r["quote_currency"] for r in body["items"]} == {"USD", "EUR", "JPY", "KWD"}
    one = world.as_user(None).get(f"{API}/rates", params={"quote": "usd"}).json()
    assert [r["quote_currency"] for r in one["items"]] == ["USD"]


def test_a_manual_rate_is_added_to_the_history_and_audited(world):
    root = world.people.root

    done = world.as_user(root).post(f"{API}/rates", json={"quote_currency": "usd", "rate": "0.0125", "pinned": True, "note": "Treasury rate"})

    assert done.status_code == 201 and done.json()["rate"] == "0.0125000000" and done.json()["source"] == "manual" and done.json()["pinned"] is True
    assert world.as_user(None).get(f"{API}/rates", params={"quote": "USD"}).json()["items"][0]["rate"] == "0.0125000000"
    history = world.as_user(root).get(f"{API}/rates/history", params={"quote": "USD"}).json()
    assert history["total"] == 2 and history["items"][0]["source"] == "manual" and history["items"][0]["created_by"] == root["id"]
    entry = world.db.query(FinanceAuditLog).filter_by(action="exchange_rate_set").one()
    assert entry.actor_id == root["id"] and Decimal(entry.before["rate"]) == Decimal("0.012") and Decimal(entry.after["rate"]) == Decimal("0.0125")
    assert world.as_user(root).post(f"{API}/rates", json={"quote_currency": "INR", "rate": "1"}).status_code == 422     # the base is always 1
    assert world.as_user(root).post(f"{API}/rates", json={"quote_currency": "USD", "rate": "0"}).status_code == 422
    assert world.as_user(root).post(f"{API}/rates", json={"quote_currency": "GBP", "rate": "0.01"}).status_code == 422   # not active


def test_the_automatic_refresh_adds_rates_but_leaves_a_pinned_one(world, monkeypatch):
    root = world.people.root
    world.as_user(root).post(f"{API}/rates", json={"quote_currency": "EUR", "rate": "0.0105", "pinned": True})
    monkeypatch.setattr(currency_service, "fetch_provider_rates", lambda base, quotes: {"USD": Decimal("0.0121"), "EUR": Decimal("0.0112"), "JPY": Decimal("1.81")})

    done = world.as_user(root).post(f"{API}/rates/refresh").json()

    assert done["updated"] == ["USD", "JPY"] and done["skipped_pinned"] == ["EUR"] and done["missing"] == ["KWD"]
    rates = {r["quote_currency"]: r["rate"] for r in world.as_user(None).get(f"{API}/rates").json()["items"]}
    assert rates["USD"] == "0.0121000000" and rates["EUR"] == "0.0105000000" and rates["KWD"] == "0.0037000000"
    assert world.as_user(root).get(f"{API}/rates/history", params={"quote": "USD"}).json()["total"] == 2


def test_a_failing_rate_service_changes_nothing(world, monkeypatch):
    def down(base, quotes):
        raise TimeoutError("no answer")

    monkeypatch.setattr(currency_service, "fetch_provider_rates", down)

    resp = world.as_user(world.people.root).post(f"{API}/rates/refresh")

    assert resp.status_code == 502 and world.db.query(ExchangeRate).count() == 5


def test_a_payment_refuses_stale_rates_but_a_display_conversion_does_not(world):
    old = datetime.utcnow() - timedelta(hours=100)
    world.db.query(ExchangeRate).filter_by(quote_currency="USD").update({"created_at": old})
    world.db.commit()

    assert world.as_user(None).get(f"{API}/convert", params={"amount": "100", "from": "INR", "to": "USD"}).status_code == 200
    try:
        currency_service.rate_between(world.db, "INR", "USD", enforce_age=True)
        raise AssertionError("expected the stale rate to be refused")
    except currency_service.RatesUnavailable as exc:
        assert exc.status_code == 503 and "out of date" in exc.detail
    row = world.db.query(ExchangeRate).filter_by(quote_currency="USD").one()
    row.pinned = True                                                                          # a pinned manual rate does not age out
    world.db.commit()
    assert currency_service.rate_between(world.db, "INR", "USD", enforce_age=True)[0] == Decimal("0.012")


def test_the_amount_limits_of_a_currency_are_enforced(world):
    world.as_user(world.people.root).patch(f"{API}/USD", json={"min_amount": "1", "max_amount": "1000"})

    currency_service.check_amount_limits(world.db, "USD", Decimal("50"))
    for amount, word in ((Decimal("0.5"), "smallest"), (Decimal("1001"), "largest")):
        try:
            currency_service.check_amount_limits(world.db, "USD", amount)
            raise AssertionError("expected a limit error")
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 422 and word in exc.detail


def test_the_preferred_currency_defaults_to_the_base_and_can_be_saved(world):
    priya = world.people.priya

    assert world.as_user(priya).get(f"{API}/preference").json() == {"currency": "INR", "is_default": True}
    assert world.as_user(priya).put(f"{API}/preference", json={"currency": "eur"}).json() == {"currency": "EUR", "is_default": False}
    assert world.as_user(priya).get(f"{API}/preference").json()["currency"] == "EUR"
    assert world.as_user(world.people.ravi).get(f"{API}/preference").json()["currency"] == "INR"        # per user
    assert world.as_user(priya).put(f"{API}/preference", json={"currency": "GBP"}).status_code == 422
    assert world.as_user(None).get(f"{API}/preference").status_code == 401
