import pytest
from datetime import datetime, timedelta, timezone
import zoneinfo
from app.utils.event_utils import get_event_lifecycle_state

class MockEvent:
    def __init__(self, start_date=None, end_date=None, time_zone="UTC", status="published"):
        self.start_date = start_date
        self.end_date = end_date
        self.time_zone = time_zone
        self.status = status

def test_lifecycle_future_event():
    # 1. Future event -> upcoming
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    event = MockEvent(
        start_date=datetime(2026, 1, 2, 12, 0),
        end_date=datetime(2026, 1, 2, 14, 0),
        time_zone="UTC"
    )
    assert get_event_lifecycle_state(event, now=now) == "upcoming"

def test_lifecycle_currently_running():
    # 2. Event currently running -> ongoing
    now = datetime(2026, 1, 2, 13, 0, tzinfo=timezone.utc)
    event = MockEvent(
        start_date=datetime(2026, 1, 2, 12, 0),
        end_date=datetime(2026, 1, 2, 14, 0),
        time_zone="UTC"
    )
    assert get_event_lifecycle_state(event, now=now) == "ongoing"

def test_lifecycle_after_end():
    # 3. Event after end -> finished
    now = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    event = MockEvent(
        start_date=datetime(2026, 1, 2, 12, 0),
        end_date=datetime(2026, 1, 2, 14, 0),
        time_zone="UTC"
    )
    assert get_event_lifecycle_state(event, now=now) == "finished"

def test_lifecycle_exact_start_boundary():
    # 4. Exact start boundary -> ongoing
    now = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    event = MockEvent(
        start_date=datetime(2026, 1, 2, 12, 0),
        end_date=datetime(2026, 1, 2, 14, 0),
        time_zone="UTC"
    )
    assert get_event_lifecycle_state(event, now=now) == "ongoing"

def test_lifecycle_exact_end_boundary():
    # 5. Exact end boundary -> ongoing
    now = datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc)
    event = MockEvent(
        start_date=datetime(2026, 1, 2, 12, 0),
        end_date=datetime(2026, 1, 2, 14, 0),
        time_zone="UTC"
    )
    assert get_event_lifecycle_state(event, now=now) == "ongoing"

def test_lifecycle_one_second_after_end():
    # 6. One second after end -> finished
    now = datetime(2026, 1, 2, 14, 0, 1, tzinfo=timezone.utc)
    event = MockEvent(
        start_date=datetime(2026, 1, 2, 12, 0),
        end_date=datetime(2026, 1, 2, 14, 0),
        time_zone="UTC"
    )
    assert get_event_lifecycle_state(event, now=now) == "finished"

def test_lifecycle_missing_end_date():
    # 7. Missing end_date -> use start_date as end boundary
    now_before = datetime(2026, 1, 2, 11, 59, 59, tzinfo=timezone.utc)
    now_exact = datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
    now_after = datetime(2026, 1, 2, 12, 0, 1, tzinfo=timezone.utc)
    
    event = MockEvent(
        start_date=datetime(2026, 1, 2, 12, 0),
        end_date=None,
        time_zone="UTC"
    )
    
    assert get_event_lifecycle_state(event, now=now_before) == "upcoming"
    assert get_event_lifecycle_state(event, now=now_exact) == "ongoing"
    assert get_event_lifecycle_state(event, now=now_after) == "finished"

def test_lifecycle_kolkata_timezone(monkeypatch):
    # Mock ZoneInfo for environments missing tzdata
    original_zoneinfo = zoneinfo.ZoneInfo
    def mock_zoneinfo(tz_str):
        if tz_str == "Asia/Kolkata":
            return timezone(timedelta(hours=5, minutes=30))
        return original_zoneinfo(tz_str)
    monkeypatch.setattr(zoneinfo, "ZoneInfo", mock_zoneinfo)
    
    # 8. Asia/Kolkata timezone
    # IST is UTC+5:30. Start date is 12:00 IST -> 06:30 UTC.
    now = datetime(2026, 1, 2, 7, 0, tzinfo=timezone.utc) # 12:30 IST
    event = MockEvent(
        start_date=datetime(2026, 1, 2, 12, 0), # 12:00 IST
        end_date=datetime(2026, 1, 2, 14, 0),   # 14:00 IST
        time_zone="Asia/Kolkata"
    )
    assert get_event_lifecycle_state(event, now=now) == "ongoing"
    
    now_before = datetime(2026, 1, 2, 6, 0, tzinfo=timezone.utc) # 11:30 IST
    assert get_event_lifecycle_state(event, now=now_before) == "upcoming"
    
    now_after = datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc) # 14:30 IST
    assert get_event_lifecycle_state(event, now=now_after) == "finished"

def test_lifecycle_invalid_timezone():
    # 10. Invalid timezone -> existing project fallback behavior (fallback to UTC)
    now = datetime(2026, 1, 2, 13, 0, tzinfo=timezone.utc)
    event = MockEvent(
        start_date=datetime(2026, 1, 2, 12, 0),
        end_date=datetime(2026, 1, 2, 14, 0),
        time_zone="Invalid/Timezone"
    )
    assert get_event_lifecycle_state(event, now=now) == "ongoing"

def test_lifecycle_missing_start_date():
    # 11. Missing start_date -> None
    now = datetime(2026, 1, 2, 13, 0, tzinfo=timezone.utc)
    event = MockEvent(
        start_date=None,
        end_date=datetime(2026, 1, 2, 14, 0),
        time_zone="UTC"
    )
    assert get_event_lifecycle_state(event, now=now) is None

def test_lifecycle_status_remains_unchanged():
    # 12. Verify status remains unchanged
    event = MockEvent(
        start_date=datetime(2020, 1, 1, 12, 0),
        end_date=datetime(2020, 1, 1, 14, 0),
        status="published",
        time_zone="UTC"
    )
    # Event is in the past
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    
    state = get_event_lifecycle_state(event, now=now)
    assert state == "finished"
    assert event.status == "published" # Does not modify status
