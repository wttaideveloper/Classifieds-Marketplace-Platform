"""
Registration-window timezone regression — "Registration Closed" shown for a window that has not opened yet.

Scenario (America/New_York event, timezone-naive timestamps in the API response):

    start_date             2026-10-11T13:40:00
    registration_open_at   2026-10-11T13:30:00     -> 17:30 UTC (EDT, UTC-4)
    registration_close_at  2026-10-11T13:39:00     -> 17:39 UTC
    registration_open      false  (window not open yet)
    lifecycle_state        "upcoming"

The backend was already correct: validate_registration_window() localizes naive values with the
EVENT's IANA timezone (get_event_timezone), preserves explicit offsets, and rejects with a *specific*
message ("not yet open" / "closed" / "ended"). The user-facing bug was Mobile collapsing every
registration_open=false into "Registration Closed". These tests pin the backend contract Mobile relies on
(and prove no guard was loosened): pure-function matrix with an injected `now`, plus HTTP-level checks that
registration, quote, checkout and waitlist all refuse a window that has not opened while staff walk-in
(which intentionally ignores the customer window) is unaffected.

Run:
    pytest tests/test_event_registration_window_timezone.py -v
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.response_mappers import _event_registration_open
from app.utils.event_utils import get_event_lifecycle_state, is_registration_open, validate_registration_window
from event_sql_support import API, client_for, customer_user, make_enterprise, make_event, make_session, silence_side_effects, staff_user

NY = ZoneInfo("America/New_York")


def utc(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def ny_event(**overrides):
    values = dict(
        time_zone="America/New_York",
        start_date=datetime(2026, 10, 11, 13, 40),
        end_date=datetime(2026, 10, 13, 18, 0),
        registration_open_at=datetime(2026, 10, 11, 13, 30),
        registration_close_at=datetime(2026, 10, 11, 13, 39),
        registration_cutoff=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def reason(event, now):
    with pytest.raises(HTTPException) as exc:
        validate_registration_window(event, now)
    assert exc.value.status_code == 400
    return exc.value.detail


class TestNewYorkNaiveWindow:
    def test_before_open_is_not_yet_open_not_closed(self):
        # 13:29 EDT == 17:29 UTC. Reading the naive value as UTC would call this "closed" hours ago.
        event = ny_event()
        assert "not yet open" in reason(event, utc(2026, 10, 11, 17, 29))
        assert is_registration_open(event, utc(2026, 10, 11, 17, 29)) is False
        assert get_event_lifecycle_state(event, utc(2026, 10, 11, 17, 29)) == "upcoming"

    def test_hours_before_open_in_utc_terms_is_still_not_yet_open(self):
        # 13:30 naive read as UTC would already be open at this instant; as New York it is not.
        assert "not yet open" in reason(ny_event(), utc(2026, 10, 11, 13, 45))

    def test_inside_window_is_open(self):
        assert is_registration_open(ny_event(), utc(2026, 10, 11, 17, 35)) is True

    def test_after_close_is_closed(self):
        assert "Registration closed" in reason(ny_event(), utc(2026, 10, 11, 17, 40))

    def test_cutoff_passed_is_distinct_from_close(self):
        event = ny_event(registration_close_at=None, registration_cutoff=datetime(2026, 10, 11, 13, 35))
        assert is_registration_open(event, utc(2026, 10, 11, 17, 34)) is True
        assert "cutoff passed" in reason(event, utc(2026, 10, 11, 17, 36))

    def test_event_already_ended_is_closed_even_without_a_window(self):
        event = ny_event(registration_open_at=None, registration_close_at=None)
        assert "already ended" in reason(event, utc(2026, 10, 13, 22, 1))  # 18:00 EDT ended at 22:00 UTC
        assert get_event_lifecycle_state(event, utc(2026, 10, 13, 22, 1)) == "finished"

    def test_event_ongoing_with_no_window_is_open(self):
        event = ny_event(registration_open_at=None, registration_close_at=None)
        assert is_registration_open(event, utc(2026, 10, 12, 12, 0)) is True
        assert get_event_lifecycle_state(event, utc(2026, 10, 12, 12, 0)) == "ongoing"


class TestExplicitOffsetsAndDst:
    def test_explicit_offset_is_trusted_not_relocalized(self):
        # 13:30-04:00 is 17:30 UTC no matter what the event zone is.
        offset = timezone(timedelta(hours=-4))
        event = ny_event(time_zone="Asia/Kolkata", registration_open_at=datetime(2026, 10, 11, 13, 30, tzinfo=offset),
                         registration_close_at=datetime(2026, 10, 11, 13, 39, tzinfo=offset))
        assert "not yet open" in reason(event, utc(2026, 10, 11, 17, 29))
        assert is_registration_open(event, utc(2026, 10, 11, 17, 35)) is True

    def test_explicit_utc_z_matches_the_equivalent_naive_new_york_value(self):
        aware = ny_event(registration_open_at=datetime(2026, 10, 11, 17, 30, tzinfo=timezone.utc),
                         registration_close_at=datetime(2026, 10, 11, 17, 39, tzinfo=timezone.utc))
        for now in (utc(2026, 10, 11, 17, 29), utc(2026, 10, 11, 17, 35), utc(2026, 10, 11, 17, 40)):
            assert is_registration_open(aware, now) == is_registration_open(ny_event(), now)

    @pytest.mark.parametrize(
        "local_open, utc_open_hour",
        [
            (datetime(2026, 7, 1, 13, 30), 17),   # summer: EDT, UTC-4
            (datetime(2026, 12, 1, 13, 30), 18),  # winter: EST, UTC-5
            (datetime(2027, 3, 13, 13, 30), 18),  # day before the 2027 spring-forward (Mar 14): still EST
            (datetime(2027, 3, 15, 13, 30), 17),  # day after: EDT
            (datetime(2026, 11, 1, 13, 30), 18),  # Nov 1 2026 fall-back already happened at 02:00: EST
            (datetime(2026, 10, 31, 13, 30), 17),  # day before fall-back: EDT
        ],
    )
    def test_new_york_dst_uses_the_tz_database_not_a_fixed_offset(self, local_open, utc_open_hour):
        event = ny_event(registration_open_at=local_open, registration_close_at=None,
                         start_date=local_open + timedelta(days=30), end_date=local_open + timedelta(days=31))
        boundary = utc(local_open.year, local_open.month, local_open.day, utc_open_hour, 30)
        assert is_registration_open(event, boundary - timedelta(minutes=1)) is False
        assert is_registration_open(event, boundary + timedelta(minutes=1)) is True


class TestResponseFlag:
    def test_registration_open_flag_is_false_before_open_with_event_zone(self, monkeypatch):
        # The API flag Mobile receives is just this boolean — it cannot say *why*, which is why Mobile must
        # derive not-started vs closed vs ended from registration_open_at / registration_close_at itself.
        monkeypatch.setattr("app.utils.event_utils._get_utc_now", lambda now=None: utc(2026, 10, 11, 17, 0))
        assert _event_registration_open(ny_event()) is False
        monkeypatch.setattr("app.utils.event_utils._get_utc_now", lambda now=None: utc(2026, 10, 11, 17, 35))
        assert _event_registration_open(ny_event()) is True


# ---------------------------------------------------------------------------------------------
# HTTP: every customer-facing mutation honours a window that has not opened yet
# ---------------------------------------------------------------------------------------------

ALL_ON = {"registration": True, "tickets": True, "sessions": False, "check_in": True, "online_meeting": False,
          "custom_questions": False, "meals": False, "accommodation": False}


@pytest.fixture
def env(monkeypatch):
    silence_side_effects(monkeypatch)
    db = make_session()
    tenant = uuid4()
    ent = make_enterprise(db, tenant)
    yield SimpleNamespace(db=db, tenant=tenant, ent=ent)
    db.close()


def _future_window_event(env, **extra):
    """New York event whose registration opens in two hours (relative to the real clock, in NY wall time)."""
    now_ny = datetime.now(NY).replace(tzinfo=None)
    return make_event(
        env.db, env.tenant, env.ent, status="published", modules=ALL_ON, capacity="50", pricing_type="paid",
        time_zone="America/New_York",
        start_date=now_ny + timedelta(days=2), end_date=now_ny + timedelta(days=3),
        registration_open_at=now_ny + timedelta(hours=2), registration_close_at=now_ny + timedelta(hours=5),
        ticket_types=[{"id": "general", "name": "General", "price": "500", "currency": "INR"}],
        **extra,
    )


class TestGuardsRejectUnopenedWindow:
    def test_registration_is_rejected(self, env):
        event = _future_window_event(env)
        resp = client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{event.id}/registrations", json={"participant_name": "A", "participant_email": "a@example.com"})
        assert resp.status_code == 400 and "not yet open" in resp.json()["detail"]

    def test_quote_is_rejected(self, env):
        event = _future_window_event(env)
        resp = client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{event.id}/checkout/quote", json={"ticket_type_id": "general", "quantity": 1})
        assert resp.status_code == 400 and "not yet open" in resp.json()["detail"]

    def test_checkout_is_rejected(self, env):
        event = _future_window_event(env)
        resp = client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{event.id}/checkout",
            json={"participant_name": "A", "participant_email": "a@example.com", "ticket_type_id": "general"})
        assert resp.status_code == 400 and "not yet open" in resp.json()["detail"]

    def test_waitlist_is_rejected(self, env):
        event = _future_window_event(env)
        resp = client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{event.id}/waitlist", json={"participant_name": "A", "participant_email": "a@example.com"})
        assert resp.status_code == 400 and "not yet open" in resp.json()["detail"]

    def test_walk_in_by_staff_intentionally_ignores_the_customer_window(self, env):
        event = _future_window_event(env)
        resp = client_for(env.db, staff_user(env.tenant, "admin")).post(
            f"{API}/{event.id}/walk-in", json={"participant_name": "W", "participant_email": "w@example.com", "check_in": True})
        assert resp.status_code in (201, 400, 422)
        # Never the customer-window message: walk-ins are gated by status + lifecycle only (event_walk_in_service).
        assert "not yet open" not in resp.text

    def test_walk_in_after_the_event_ended_is_still_rejected(self, env):
        now_ny = datetime.now(NY).replace(tzinfo=None)
        event = make_event(env.db, env.tenant, env.ent, status="published", modules=ALL_ON, capacity="50",
                           time_zone="America/New_York",
                           start_date=now_ny - timedelta(days=3), end_date=now_ny - timedelta(days=2))
        resp = client_for(env.db, staff_user(env.tenant, "admin")).post(
            f"{API}/{event.id}/walk-in", json={"participant_name": "W", "participant_email": "w@example.com"})
        assert resp.status_code == 400 and "finished" in resp.json()["detail"]
