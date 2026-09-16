import pytest
from datetime import datetime, timezone
import zoneinfo
from fastapi import HTTPException

from app.utils.event_utils import is_registration_open, validate_registration_window

class MockEvent:
    def __init__(self, time_zone="Asia/Kolkata", open_at=None, close_at=None, cutoff=None):
        self.time_zone = time_zone
        self.registration_open_at = open_at
        self.registration_close_at = close_at
        self.registration_cutoff = cutoff

def create_naive_dt(dt_str):
    if not dt_str:
        return None
    return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")

def create_aware_utc_now(dt_str):
    # Parse as UTC directly
    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=timezone.utc)

def test_asia_kolkata_before_opening():
    # A. Asia/Kolkata before opening
    # open = 2026-09-16 19:49
    # Current: 19:48 IST -> 14:18 UTC
    event = MockEvent(
        time_zone="Asia/Kolkata",
        open_at=create_naive_dt("2026-09-16 19:49:00"),
        close_at=create_naive_dt("2026-09-17 21:47:00")
    )
    now = create_aware_utc_now("2026-09-16 14:18:00")
    assert is_registration_open(event, now) is False
    with pytest.raises(HTTPException) as exc:
        validate_registration_window(event, now)
    assert "Registration not yet open" in str(exc.value.detail)

def test_asia_kolkata_during_open_window():
    # B. Asia/Kolkata during open window
    # Current: 19:58 IST -> 14:28 UTC
    event = MockEvent(
        time_zone="Asia/Kolkata",
        open_at=create_naive_dt("2026-09-16 19:49:00"),
        close_at=create_naive_dt("2026-09-17 21:47:00")
    )
    now = create_aware_utc_now("2026-09-16 14:28:00")
    assert is_registration_open(event, now) is True

def test_asia_kolkata_exactly_at_closing():
    # C. Exactly at closing
    # Exactly at closing 21:47 IST means 21:47:00, typically open checks are > close, but wait, the spec says "Exactly at closing -> Expected: false"
    # Actually if current_time > close_time, it closes. If the requirement is Exactly at closing = false, let's see current code:
    # current_utc > close_at_utc. So exactly at closing (current_utc == close_at_utc) would evaluate to False > False -> False -> meaning it's OPEN.
    # But the spec says: "C. Exactly at closing -> Expected: false"
    # If the user explicitly wants false, I might need to change `>` to `>=`.
    pass

def test_asia_kolkata_after_closing():
    # D. After closing
    # Current: 21:48 IST -> 16:18 UTC (next day)
    event = MockEvent(
        time_zone="Asia/Kolkata",
        open_at=create_naive_dt("2026-09-16 19:49:00"),
        close_at=create_naive_dt("2026-09-17 21:47:00")
    )
    now = create_aware_utc_now("2026-09-17 16:18:00")
    assert is_registration_open(event, now) is False
    with pytest.raises(HTTPException) as exc:
        validate_registration_window(event, now)
    assert "Registration closed" in str(exc.value.detail)

def test_no_closing_time():
    # E. No closing time
    event = MockEvent(
        time_zone="Asia/Kolkata",
        open_at=create_naive_dt("2026-09-16 19:49:00"),
        close_at=None
    )
    # Future date: 2027
    now = create_aware_utc_now("2027-09-16 14:28:00")
    assert is_registration_open(event, now) is True

def test_before_opening_another_day():
    # F. Before opening on another day
    event = MockEvent(
        time_zone="Asia/Kolkata",
        open_at=create_naive_dt("2026-09-16 19:49:00"),
        close_at=create_naive_dt("2026-09-17 21:47:00")
    )
    # Request on 15th
    now = create_aware_utc_now("2026-09-15 14:28:00")
    assert is_registration_open(event, now) is False

def test_america_new_york():
    # G. America/New_York
    # Event local: 19:49 NY time on 2026-09-16
    # 2026-09-16 is EDT (UTC-4)
    # 19:49 EDT is 23:49 UTC
    event = MockEvent(
        time_zone="America/New_York",
        open_at=create_naive_dt("2026-09-16 19:49:00"),
        close_at=create_naive_dt("2026-09-17 21:47:00")
    )
    # Before opening: 23:48 UTC
    before = create_aware_utc_now("2026-09-16 23:48:00")
    assert is_registration_open(event, before) is False
    
    # During open: 23:58 UTC
    during = create_aware_utc_now("2026-09-16 23:58:00")
    assert is_registration_open(event, during) is True

def test_europe_london():
    # H. Europe/London
    # Event local: 19:49 London time on 2026-09-16
    # 2026-09-16 is BST (UTC+1)
    # 19:49 BST is 18:49 UTC
    event = MockEvent(
        time_zone="Europe/London",
        open_at=create_naive_dt("2026-09-16 19:49:00"),
        close_at=create_naive_dt("2026-09-17 21:47:00")
    )
    # Before opening: 18:48 UTC
    before = create_aware_utc_now("2026-09-16 18:48:00")
    assert is_registration_open(event, before) is False
    
    # During open: 18:58 UTC
    during = create_aware_utc_now("2026-09-16 18:58:00")
    assert is_registration_open(event, during) is True

def test_asia_kolkata_exactly_at_closing_fixed():
    # Exactly at closing 21:47 IST -> 16:17 UTC
    event = MockEvent(
        time_zone="Asia/Kolkata",
        open_at=create_naive_dt("2026-09-16 19:49:00"),
        close_at=create_naive_dt("2026-09-17 21:47:00")
    )
    now = create_aware_utc_now("2026-09-17 16:17:00")
    # If the user expects it to be False at exactly the closing time,
    # the condition `now >= close_at_utc` would be needed. 
    assert is_registration_open(event, now) is False
    with pytest.raises(HTTPException) as exc:
        validate_registration_window(event, now)
    assert "Registration closed" in str(exc.value.detail)
