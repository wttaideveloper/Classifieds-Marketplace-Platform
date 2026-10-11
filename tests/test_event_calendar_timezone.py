"""
Calendar export (ICS) and "my" endpoints honour the Event timezone.

Bug: calendar_service._fmt() stamped a NAIVE start/end (event-local wall-clock, see docs/event-timezone-contract.md)
with "Z" as if it were UTC, so a 13:40 America/New_York event was exported as 13:40Z (9:40 AM EDT) — 4h early —
and a 18:30 Asia/Kolkata event as 18:30Z (midnight IST). Sessions (session_date + start_time) had the same flaw.
Fix: naive values are read in Event.time_zone, then written as UTC. Format is unchanged (UTC "Z" stamps).

Run:
    pytest tests/test_event_calendar_timezone.py -v
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.calendar_service import event_to_ics
from event_sql_support import API, client_for, customer_user, make_enterprise, make_event, make_registration, make_session, make_waitlist, silence_side_effects


def ics_event(**overrides):
    values = dict(
        id=uuid4(), title="T", description="D", venue=None, sessions=[], time_zone="America/New_York",
        start_date=datetime(2026, 10, 11, 13, 40), end_date=datetime(2026, 10, 13, 18, 0),
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def field(ics: str, name: str, nth: int = 0) -> str:
    return [line.split(":", 1)[1] for line in ics.split("\r\n") if line.startswith(name + ":")][nth]


class TestIcsInstants:
    def test_new_york_summer_edt(self):
        ics = event_to_ics(ics_event())
        assert field(ics, "DTSTART") == "20261011T174000Z"  # 13:40 EDT
        assert field(ics, "DTEND") == "20261013T220000Z"  # 18:00 EDT

    def test_new_york_winter_est_uses_the_tz_database(self):
        ics = event_to_ics(ics_event(start_date=datetime(2026, 12, 1, 13, 40), end_date=datetime(2026, 12, 1, 15, 0)))
        assert field(ics, "DTSTART") == "20261201T184000Z"  # EST, UTC-5

    def test_kolkata(self):
        ics = event_to_ics(ics_event(time_zone="Asia/Kolkata", start_date=datetime(2026, 10, 11, 18, 30),
                                     end_date=datetime(2026, 10, 11, 20, 0)))
        assert field(ics, "DTSTART") == "20261011T130000Z"
        assert field(ics, "DTEND") == "20261011T143000Z"

    def test_dst_transition_day(self):
        before = event_to_ics(ics_event(start_date=datetime(2027, 3, 14, 1, 30), end_date=datetime(2027, 3, 14, 1, 45)))
        after = event_to_ics(ics_event(start_date=datetime(2027, 3, 14, 3, 30), end_date=datetime(2027, 3, 14, 3, 45)))
        assert field(before, "DTSTART") == "20270314T063000Z"  # still EST
        assert field(after, "DTSTART") == "20270314T073000Z"  # EDT

    def test_explicit_offsets_and_utc_keep_their_instant(self):
        ics = event_to_ics(ics_event(
            time_zone="Asia/Kolkata",
            start_date=datetime(2026, 10, 11, 13, 40, tzinfo=timezone(timedelta(hours=-4))),
            end_date=datetime(2026, 10, 13, 22, 0, tzinfo=timezone.utc)))
        assert field(ics, "DTSTART") == "20261011T174000Z"
        assert field(ics, "DTEND") == "20261013T220000Z"

    def test_missing_or_invalid_zone_is_utc_like_the_rest_of_the_backend(self):
        for zone in (None, "", "Mars/Base"):
            assert field(event_to_ics(ics_event(time_zone=zone)), "DTSTART") == "20261011T134000Z"

    def test_object_without_a_time_zone_attribute_is_unchanged(self):
        # The training calendar endpoint feeds an event-like object that has no time_zone: still UTC, as before.
        e = ics_event()
        del e.time_zone
        assert field(event_to_ics(e), "DTSTART") == "20261011T134000Z"

    def test_missing_end_defaults_to_one_hour_after_start(self):
        ics = event_to_ics(ics_event(end_date=None))
        assert field(ics, "DTEND") == "20261011T184000Z"

    def test_format_is_still_valid_ics(self):
        ics = event_to_ics(ics_event())
        assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR")
        assert "SUMMARY:T" in ics and "METHOD:PUBLISH" in ics
        assert all(len(v) == 16 and v.endswith("Z") for v in (field(ics, "DTSTART"), field(ics, "DTEND"), field(ics, "DTSTAMP")))


class TestIcsSessions:
    def test_session_wall_clock_is_read_in_the_event_zone(self):
        sessions = [{"id": "s1", "title": "Keynote", "session_date": "2026-10-11", "start_time": "13:00", "end_time": "14:30"}]
        ics = event_to_ics(ics_event(sessions=sessions))
        assert field(ics, "DTSTART", 1) == "20261011T170000Z"  # 13:00 EDT
        assert field(ics, "DTEND", 1) == "20261011T183000Z"

    def test_session_in_winter_and_in_kolkata(self):
        winter = [{"id": "s1", "title": "K", "session_date": "2026-12-01", "start_time": "13:00", "end_time": "14:00"}]
        assert field(event_to_ics(ics_event(sessions=winter)), "DTSTART", 1) == "20261201T180000Z"
        assert field(event_to_ics(ics_event(time_zone="Asia/Kolkata", sessions=winter)), "DTSTART", 1) == "20261201T073000Z"


class TestHttp:
    @pytest.fixture
    def env(self, monkeypatch):
        silence_side_effects(monkeypatch)
        db = make_session()
        tenant = uuid4()
        yield SimpleNamespace(db=db, tenant=tenant, ent=make_enterprise(db, tenant))
        db.close()

    def test_calendar_endpoints_export_the_event_zone_instant(self, env):
        event = make_event(env.db, env.tenant, env.ent, status="published", time_zone="America/New_York",
                           start_date=datetime(2026, 10, 11, 13, 40), end_date=datetime(2026, 10, 13, 18, 0),
                           sessions=[{"id": "s1", "title": "Keynote", "session_date": "2026-10-11", "start_time": "13:00", "end_time": "14:30"}])
        client = client_for(env.db, customer_user("a@example.com"))
        whole = client.get(f"{API}/{event.id}/calendar.ics")
        assert whole.status_code == 200 and "text/calendar" in whole.headers["content-type"]
        assert "DTSTART:20261011T174000Z" in whole.text
        single = client.get(f"{API}/{event.id}/sessions/s1/calendar.ics")
        assert single.status_code == 200 and "DTSTART:20261011T170000Z" in single.text

    def test_my_registrations_and_waitlist_expose_event_time_zone(self, env):
        event = make_event(env.db, env.tenant, env.ent, status="published", time_zone="America/New_York",
                           start_date=datetime(2026, 10, 11, 13, 40), end_date=datetime(2026, 10, 13, 18, 0))
        make_registration(env.db, event, "a@example.com")
        make_waitlist(env.db, event, "a@example.com")
        client = client_for(env.db, customer_user("a@example.com"))
        regs = client.get(f"{API}/my/registrations").json()
        assert regs[0]["event_start"] == "2026-10-11T13:40:00" and regs[0]["event_time_zone"] == "America/New_York"
        waits = client.get(f"{API}/my/waitlist").json()
        assert waits[0]["event_start_date"] == "2026-10-11T13:40:00" and waits[0]["event_time_zone"] == "America/New_York"
