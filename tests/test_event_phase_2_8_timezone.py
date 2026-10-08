"""
Phase 2.8 timezone regression — Mobile/Backend purchase-window disagreement.

Bug: an organizer enters a meal/accommodation purchase window as their own local wall-clock time
(e.g. "12:00" -> "15:00", meaning 12:00-15:00 IST for an Asia/Kolkata event) via a plain
datetime-local-style input, which is stored naive (no timezone offset) — see
app.utils.event_meals._normalize_window_value / app.utils.event_accommodation._normalize_window_value:
"never guesses a timezone that isn't there". is_within_purchase_window() used to compare that
naive string directly against datetime.utcnow(), i.e. silently treated "12:00" as 12:00 UTC
(17:30 IST) instead of 12:00 IST (06:30 UTC) — a 5.5h misinterpretation. Mobile independently
fixed its own display-side parsing to read a naive window as the event's IST wall-clock time
(app/utils/event.mapper.ts), which made the backend's checkout/quote endpoint disagree with what
Mobile displayed as available ("Meal option(s) outside their purchase window" for a window Mobile
correctly showed as open).

Fix: app.utils.event_utils.get_event_timezone(event) (renamed from the registration-window-only
_get_event_tz, now the single canonical resolver) + the new resolve_naive_or_aware() interpret a
naive purchase/service-window timestamp as the EVENT's configured timezone (Event.time_zone,
default "Asia/Kolkata") — the same canonical pattern validate_registration_window() already uses
for registration_open_at/close_at/cutoff. An already timezone-aware stored value (explicit
offset or Z) is trusted as-is and never re-localized, so pre-existing aware/UTC data is
unaffected (backward compatibility).

This file covers:
  1. Pure, deterministic boundary-matrix tests directly against is_within_purchase_window() for
     both meals and accommodation, at a fixed reference "now" — no clock mocking needed.
  2. Backward compatibility: an explicit +05:30 offset and an explicit UTC ("Z") value for the
     SAME wall-clock instant must behave identically to the naive value.
  3. service_end_at blocks a new selection after fulfilment has completed, while
     service_start_at still permits advance purchase.
  4. End-to-end HTTP regression (the user-facing report): checkout/quote, checkout, free
     registration, and walk-in all agree that a naive-window option is purchasable right now.

Run:
    pytest tests/test_event_phase_2_8_timezone.py -v
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.utils.event_accommodation import is_within_purchase_window as accommodation_is_within_purchase_window
from app.utils.event_meals import is_within_purchase_window as meal_is_within_purchase_window
from app.utils.event_utils import get_event_timezone, resolve_naive_or_aware
from event_sql_support import API, EventOrder, EventRegistration, client_for, customer_user, make_enterprise, make_event, make_session, silence_side_effects, staff_user

ALL_ON = {"registration": True, "tickets": True, "sessions": False, "check_in": True, "online_meeting": False,
          "custom_questions": False, "meals": True, "accommodation": True}

IST = ZoneInfo("Asia/Kolkata")
KOLKATA_EVENT = SimpleNamespace(time_zone="Asia/Kolkata")

# The exact window from the bug report: organizer enters 12:00 -> 15:00, meaning IST.
WINDOW_START_NAIVE = "2026-10-01T12:00:00"
WINDOW_END_NAIVE = "2026-10-01T15:00:00"


def _at(hhmm: str) -> datetime:
    """2026-10-01 HH:MM:SS in IST -> aware UTC 'now', for the boundary matrix below."""
    return datetime.fromisoformat(f"2026-10-01T{hhmm}+05:30").astimezone(timezone.utc)


# ===========================================================================
# 1. Pure boundary matrix — the user's required A-G-equivalent test case
# ===========================================================================


@pytest.mark.parametrize("is_within_purchase_window", [meal_is_within_purchase_window, accommodation_is_within_purchase_window], ids=["meal", "accommodation"])
class TestNaiveWindowInterpretedAsEventTimezone:
    @pytest.mark.parametrize("now_ist,expected", [
        ("11:59:00", False),  # before purchase_start_at -> closed
        ("12:00:00", True),   # at purchase_start_at -> open
        ("13:00:00", True),   # inside the window (the user's exact "13:00 IST" case) -> open
        ("15:00:00", False),  # at purchase_end_at -> existing boundary rule (>=) -> closed, unchanged by this fix
        ("15:01:00", False),  # after purchase_end_at -> closed
    ])
    def test_naive_window_matches_organizer_local_wall_clock(self, is_within_purchase_window, now_ist, expected):
        option = {"purchase_start_at": WINDOW_START_NAIVE, "purchase_end_at": WINDOW_END_NAIVE}
        event_tz = get_event_timezone(KOLKATA_EVENT)
        assert is_within_purchase_window(option, _at(now_ist), event_tz) is expected

    def test_explicit_offset_matches_the_naive_result_for_the_same_instant(self, is_within_purchase_window):
        """Backward compatibility: a pre-existing event whose organizer form DID send an explicit
        +05:30 offset must behave exactly like the naive case above — unaffected by this fix."""
        option = {"purchase_start_at": "2026-10-01T12:00:00+05:30", "purchase_end_at": "2026-10-01T15:00:00+05:30"}
        event_tz = get_event_timezone(KOLKATA_EVENT)
        assert is_within_purchase_window(option, _at("13:00:00"), event_tz) is True
        assert is_within_purchase_window(option, _at("11:59:00"), event_tz) is False

    def test_explicit_utc_z_matches_the_naive_result_for_the_same_instant(self, is_within_purchase_window):
        """Backward compatibility: a pre-existing event whose organizer form sent a real UTC
        timestamp (06:30-09:30 UTC == 12:00-15:00 IST) must also keep working unchanged."""
        option = {"purchase_start_at": "2026-10-01T06:30:00Z", "purchase_end_at": "2026-10-01T09:30:00Z"}
        event_tz = get_event_timezone(KOLKATA_EVENT)
        assert is_within_purchase_window(option, _at("13:00:00"), event_tz) is True
        assert is_within_purchase_window(option, _at("11:59:00"), event_tz) is False

    def test_a_non_kolkata_event_timezone_is_honoured_not_hardcoded(self, is_within_purchase_window):
        """Proves the fix uses the event's OWN configured timezone, not a hardcoded IST offset."""
        ny_event = SimpleNamespace(time_zone="America/New_York")
        event_tz = get_event_timezone(ny_event)
        option = {"purchase_start_at": "2026-10-01T12:00:00", "purchase_end_at": "2026-10-01T15:00:00"}
        now_1pm_ny = datetime.fromisoformat("2026-10-01T13:00:00-04:00").astimezone(timezone.utc)
        assert is_within_purchase_window(option, now_1pm_ny, event_tz) is True
        # The SAME absolute instant is 22:30 IST -- well outside 12:00-15:00 -- proving the two
        # interpretations genuinely diverge and the event's own tz, not IST, was applied.
        assert is_within_purchase_window(option, now_1pm_ny, get_event_timezone(KOLKATA_EVENT)) is False

    def test_no_window_configured_is_always_open(self, is_within_purchase_window):
        event_tz = get_event_timezone(KOLKATA_EVENT)
        assert is_within_purchase_window({}, datetime.now(timezone.utc), event_tz) is True

    def test_service_window_does_not_change_the_purchase_window_result(self, is_within_purchase_window):
        """Purchase-window evaluation remains independent; new-selection service-end
        enforcement is applied separately by the shared selection validators."""
        option = {
            "purchase_start_at": WINDOW_START_NAIVE, "purchase_end_at": WINDOW_END_NAIVE,
            "service_start_at": "2099-01-01T00:00:00", "service_end_at": "2099-01-02T00:00:00",
        }
        event_tz = get_event_timezone(KOLKATA_EVENT)
        assert is_within_purchase_window(option, _at("13:00:00"), event_tz) is True


def test_resolve_naive_or_aware_localizes_naive_and_trusts_aware():
    event_tz = get_event_timezone(KOLKATA_EVENT)
    naive_noon = datetime.fromisoformat(WINDOW_START_NAIVE)
    assert resolve_naive_or_aware(naive_noon, event_tz) == datetime.fromisoformat("2026-10-01T06:30:00+00:00")

    aware = datetime.fromisoformat("2026-10-01T12:00:00+05:30")
    assert resolve_naive_or_aware(aware, event_tz) == datetime.fromisoformat("2026-10-01T06:30:00+00:00")

    assert resolve_naive_or_aware(None, event_tz) is None


# ===========================================================================
# 2. End-to-end HTTP regression — quote / checkout / free registration / walk-in all agree
# ===========================================================================


@pytest.fixture
def env(monkeypatch):
    apply_async = silence_side_effects(monkeypatch)
    db = make_session()
    tenant = uuid4()
    ent = make_enterprise(db, tenant)
    monkeypatch.setattr(
        "app.services.event_form_config_service.apply_form_configuration_to_event_data",
        lambda _db, data, user: {"tenant_id": tenant, "enterprise_id": ent.id, "form_configuration_id": None,
                                 "form_configuration_version_id": None, "custom_values": []},
    )
    yield SimpleNamespace(db=db, tenant=tenant, ent=ent, owner=staff_user(tenant, "admin"))
    db.close()


def _naive_window_open_now_ist():
    """A purchase window naively entered as local wall-clock time, open right now -- relative to
    the real clock (no freezegun in this suite), mirroring how other Event tests avoid mocking time
    (see test_event_phase_2_6_meals.py's `datetime.utcnow() + timedelta(...)` convention)."""
    now_ist_naive = datetime.now(IST).replace(tzinfo=None)
    start = (now_ist_naive - timedelta(hours=1)).isoformat()
    end = (now_ist_naive + timedelta(hours=1)).isoformat()
    return start, end


def _naive_window_already_closed():
    now_ist_naive = datetime.now(IST).replace(tzinfo=None)
    start = (now_ist_naive - timedelta(hours=3)).isoformat()
    end = (now_ist_naive - timedelta(hours=1)).isoformat()
    return start, end


class TestEndToEndAgreement:
    def test_quote_accepts_a_naive_window_option_open_right_now(self, env):
        start, end = _naive_window_open_now_ist()
        event = make_event(
            env.db, env.tenant, env.ent, status="published", modules=ALL_ON, capacity="50",
            pricing_type="paid", time_zone="Asia/Kolkata",
            ticket_types=[{"id": "general", "name": "General", "price": "500", "currency": "INR"}],
            meals={"options": [{"id": "lunch", "name": "Lunch", "price": "100", "currency": "INR",
                                 "purchase_start_at": start, "purchase_end_at": end}]},
            accommodation={"options": [{"id": "shared-room", "name": "Shared Room", "price": "200", "currency": "INR",
                                         "purchase_start_at": start, "purchase_end_at": end}]},
        )
        resp = client_for(env.db, customer_user("a@example.com")).post(f"{API}/{event.id}/checkout/quote", json={
            "ticket_type_id": "general", "quantity": 1, "meal_selections": ["lunch"], "accommodation_selections": ["shared-room"]})
        assert resp.status_code == 200, resp.text
        assert "outside their purchase window" not in resp.text

    def test_quote_rejects_a_naive_window_option_already_closed(self, env):
        start, end = _naive_window_already_closed()
        event = make_event(
            env.db, env.tenant, env.ent, status="published", modules=ALL_ON, capacity="50",
            pricing_type="paid", time_zone="Asia/Kolkata",
            ticket_types=[{"id": "general", "name": "General", "price": "500", "currency": "INR"}],
            meals={"options": [{"id": "lunch", "name": "Lunch", "price": "100", "currency": "INR",
                                 "purchase_start_at": start, "purchase_end_at": end}]},
        )
        resp = client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{event.id}/checkout/quote", json={"ticket_type_id": "general", "meal_selections": ["lunch"]})
        assert resp.status_code == 422 and "outside their purchase window" in resp.json()["detail"]

    def test_checkout_accepts_the_same_naive_window_quote_already_accepted(self, env):
        start, end = _naive_window_open_now_ist()
        event = make_event(
            env.db, env.tenant, env.ent, status="published", modules=ALL_ON, capacity="50",
            pricing_type="paid", time_zone="Asia/Kolkata",
            ticket_types=[{"id": "general", "name": "General", "price": "500", "currency": "INR"}],
            meals={"options": [{"id": "lunch", "name": "Lunch", "price": "100", "currency": "INR",
                                 "purchase_start_at": start, "purchase_end_at": end}]},
        )
        resp = client_for(env.db, customer_user("a@example.com")).post(f"{API}/{event.id}/checkout", json={
            "participant_name": "A", "participant_email": "a@example.com",
            "ticket_type_id": "general", "meal_selections": ["lunch"]})
        assert resp.status_code == 201, resp.text

    def test_free_registration_accepts_the_same_naive_window(self, env):
        start, end = _naive_window_open_now_ist()
        event = make_event(
            env.db, env.tenant, env.ent, status="published", modules=ALL_ON, capacity="50",
            pricing_type="free", time_zone="Asia/Kolkata",
            meals={"options": [{"id": "lunch", "name": "Lunch", "price": "100", "currency": "INR",
                                 "purchase_start_at": start, "purchase_end_at": end}]},
        )
        resp = client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{event.id}/registrations",
            json={"participant_name": "A", "participant_email": "a@example.com", "meal_selections": ["lunch"]})
        assert resp.status_code == 201, resp.text
        assert env.db.query(EventOrder).count() == 1  # priced -> a real order, proving the selection was accepted

    def test_walk_in_accepts_the_same_naive_window(self, env):
        start, end = _naive_window_open_now_ist()
        event = make_event(
            env.db, env.tenant, env.ent, status="published", modules=ALL_ON, capacity="50",
            pricing_type="free", time_zone="Asia/Kolkata",
            accommodation={"options": [{"id": "shared-room", "name": "Shared Room", "price": "200", "currency": "INR",
                                         "purchase_start_at": start, "purchase_end_at": end}]},
        )
        resp = client_for(env.db, env.owner).post(f"{API}/{event.id}/walk-in", json={
            "participant_name": "W", "participant_email": "w@example.com",
            "accommodation_selections": ["shared-room"], "check_in": True})
        assert resp.status_code == 201, resp.text
        assert "outside their purchase window" not in resp.text
        assert env.db.query(EventRegistration).count() == 1

    def test_quote_also_accepts_an_explicit_offset_window_open_right_now(self, env):
        """Backward compatibility end-to-end: an event whose organizer form already sent an
        explicit offset keeps working through the full HTTP stack, not just the pure function."""
        now_ist = datetime.now(IST)
        start = (now_ist - timedelta(hours=1)).isoformat()
        end = (now_ist + timedelta(hours=1)).isoformat()
        event = make_event(
            env.db, env.tenant, env.ent, status="published", modules=ALL_ON, capacity="50",
            pricing_type="paid", time_zone="Asia/Kolkata",
            ticket_types=[{"id": "general", "name": "General", "price": "500", "currency": "INR"}],
            meals={"options": [{"id": "lunch", "name": "Lunch", "price": "100", "currency": "INR",
                                 "purchase_start_at": start, "purchase_end_at": end}]},
        )
        resp = client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{event.id}/checkout/quote", json={"ticket_type_id": "general", "meal_selections": ["lunch"]})
        assert resp.status_code == 200, resp.text
