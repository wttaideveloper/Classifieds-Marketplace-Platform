"""
Event timestamp WRITE contract — create/update round trip.

Contract (docs/event-timezone-contract.md): start_date / end_date / registration_open_at / registration_close_at /
registration_cutoff are stored as naive EVENT-LOCAL wall-clock time in ``Event.time_zone`` (an IANA key).

Bugs this pins down (all reproduced against the previous code):
  * A timestamp with an explicit offset / Z had its offset silently dropped by the naive column, so the digits
    were later re-read as event-local: "17:30Z" on a New York event was stored as 17:30 (= 21:30Z), 4h late.
  * time_zone was never validated: "Mars/Base" was accepted and silently evaluated as UTC afterwards.
  * PUT {"time_zone": null} overwrote the stored zone with NULL (-> UTC at validation time).

Run:
    pytest tests/test_event_timezone_write_contract.py -v
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from app.models.event_model import Event
from event_sql_support import API, client_for, customer_user, make_enterprise, make_event, make_session, silence_side_effects, staff_user

NY = ZoneInfo("America/New_York")

BASE = {"title": "T", "category": "Wellness", "pricing_type": "free", "event_type": "other",
        "modules": {"registration": True}}


@pytest.fixture
def env(monkeypatch):
    silence_side_effects(monkeypatch)
    db = make_session()
    tenant = uuid4()
    ent = make_enterprise(db, tenant)
    monkeypatch.setattr(
        "app.services.event_form_config_service.apply_form_configuration_to_event_data",
        lambda _db, data, user: {"tenant_id": tenant, "enterprise_id": ent.id, "form_configuration_id": None,
                                 "form_configuration_version_id": None, "custom_values": []},
    )
    monkeypatch.setattr("app.services.event_form_config_service.apply_form_configuration_to_event_update",
                        lambda *a, **k: None)
    yield SimpleNamespace(db=db, tenant=tenant, ent=ent, admin=client_for(db, staff_user(tenant, "admin")))
    db.close()


def stored(env, event_id) -> Event:
    row = env.db.query(Event).filter(Event.id == UUID(str(event_id))).first()
    env.db.refresh(row)
    return row


def create(env, **fields):
    return env.admin.post(f"{API}/", json={**BASE, **fields})


NY_NAIVE = dict(time_zone="America/New_York", start_date="2026-10-11T13:40:00", end_date="2026-10-13T18:00:00",
                registration_open_at="2026-10-11T13:30:00", registration_close_at="2026-10-11T13:39:00")


class TestCreateRoundTrip:
    def test_naive_wall_clock_and_zone_are_stored_and_returned_unchanged(self, env):
        resp = create(env, **NY_NAIVE)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["time_zone"] == "America/New_York"
        assert (body["start_date"], body["end_date"]) == ("2026-10-11T13:40:00", "2026-10-13T18:00:00")
        assert (body["registration_open_at"], body["registration_close_at"]) == ("2026-10-11T13:30:00", "2026-10-11T13:39:00")
        row = stored(env, body["id"])
        assert row.time_zone == "America/New_York" and row.registration_open_at == datetime(2026, 10, 11, 13, 30)

    @pytest.mark.parametrize("suffix", ["-04:00", "Z"])
    def test_explicit_offset_is_converted_once_to_event_local_wall_clock(self, env, suffix):
        # 13:30 EDT == 17:30Z. Both spellings must land on the same stored wall-clock.
        wall = "13:30:00-04:00" if suffix == "-04:00" else "17:30:00Z"
        close = "13:39:00-04:00" if suffix == "-04:00" else "17:39:00Z"
        start = "13:40:00-04:00" if suffix == "-04:00" else "17:40:00Z"
        resp = create(env, time_zone="America/New_York", start_date=f"2026-10-11T{start}",
                      end_date="2026-10-13T22:00:00Z", registration_open_at=f"2026-10-11T{wall}",
                      registration_close_at=f"2026-10-11T{close}")
        assert resp.status_code == 201, resp.text
        row = stored(env, resp.json()["id"])
        assert row.registration_open_at == datetime(2026, 10, 11, 13, 30)
        assert row.registration_close_at == datetime(2026, 10, 11, 13, 39)
        assert row.start_date == datetime(2026, 10, 11, 13, 40)
        assert row.end_date == datetime(2026, 10, 13, 18, 0)

    def test_offset_conversion_follows_dst_not_a_fixed_offset(self, env):
        # 2026-12-01 is EST (UTC-5): 18:30Z is 13:30 local. 2026-07-01 is EDT (UTC-4): 17:30Z is 13:30 local.
        for utc, expected in (("2026-12-01T18:30:00Z", datetime(2026, 12, 1, 13, 30)),
                              ("2026-07-01T17:30:00Z", datetime(2026, 7, 1, 13, 30))):
            resp = create(env, time_zone="America/New_York", start_date=utc, end_date=utc, registration_open_at=utc)
            assert resp.status_code == 201, resp.text
            assert stored(env, resp.json()["id"]).registration_open_at == expected

    def test_offset_in_a_different_zone_than_the_event_keeps_the_instant(self, env):
        # 23:00+05:30 (IST) == 17:30Z == 13:30 New York
        resp = create(env, time_zone="America/New_York", start_date="2026-10-11T23:10:00+05:30",
                      end_date="2026-10-14T03:30:00+05:30", registration_open_at="2026-10-11T23:00:00+05:30")
        assert stored(env, resp.json()["id"]).registration_open_at == datetime(2026, 10, 11, 13, 30)

    def test_omitted_or_null_time_zone_gets_the_explicit_platform_default(self, env):
        for fields in ({}, {"time_zone": None}, {"time_zone": "  "}):
            resp = create(env, start_date="2026-10-11T13:40:00", end_date="2026-10-13T18:00:00", **fields)
            assert resp.status_code == 201, resp.text
            assert resp.json()["time_zone"] == "Asia/Kolkata"
            assert stored(env, resp.json()["id"]).time_zone == "Asia/Kolkata"

    def test_invalid_time_zone_is_rejected_not_silently_treated_as_utc(self, env):
        resp = create(env, time_zone="Mars/Base", start_date="2026-10-11T13:40:00", end_date="2026-10-13T18:00:00")
        assert resp.status_code == 422 and "time_zone" in resp.text


class TestUpdateRoundTrip:
    def _event(self, env):
        return create(env, **NY_NAIVE).json()["id"]

    def test_offset_only_update_uses_the_stored_event_zone(self, env):
        eid = self._event(env)
        resp = env.admin.put(f"{API}/{eid}", json={"registration_open_at": "2026-10-11T17:35:00Z"})
        assert resp.status_code == 200, resp.text
        assert stored(env, eid).registration_open_at == datetime(2026, 10, 11, 13, 35)

    def test_offset_update_with_new_zone_in_the_same_request_uses_the_new_zone(self, env):
        eid = self._event(env)
        resp = env.admin.put(f"{API}/{eid}", json={"time_zone": "Asia/Kolkata", "registration_open_at": "2026-10-11T17:30:00Z"})
        assert resp.status_code == 200, resp.text
        row = stored(env, eid)
        assert row.time_zone == "Asia/Kolkata" and row.registration_open_at == datetime(2026, 10, 11, 23, 0)

    def test_naive_update_is_stored_verbatim(self, env):
        eid = self._event(env)
        env.admin.put(f"{API}/{eid}", json={"registration_open_at": "2026-10-11T09:00:00"})
        assert stored(env, eid).registration_open_at == datetime(2026, 10, 11, 9, 0)

    def test_null_time_zone_never_overwrites_the_stored_zone(self, env):
        eid = self._event(env)
        assert env.admin.put(f"{API}/{eid}", json={"time_zone": None}).status_code == 200
        assert stored(env, eid).time_zone == "America/New_York"

    def test_invalid_time_zone_is_rejected_and_nothing_changes(self, env):
        eid = self._event(env)
        resp = env.admin.put(f"{API}/{eid}", json={"time_zone": "Mars/Base"})
        assert resp.status_code == 422
        assert stored(env, eid).time_zone == "America/New_York"

    def test_changing_only_the_zone_does_not_rewrite_stored_timestamps(self, env):
        eid = self._event(env)
        env.admin.put(f"{API}/{eid}", json={"time_zone": "Europe/London"})
        row = stored(env, eid)
        assert row.time_zone == "Europe/London"
        assert (row.start_date, row.registration_open_at) == (datetime(2026, 10, 11, 13, 40), datetime(2026, 10, 11, 13, 30))


class TestExistingRecordsStayUnchanged:
    """Rows written before this contract are never rewritten or reinterpreted by reads or unrelated edits."""

    def _legacy(self, env, time_zone):
        event = make_event(env.db, env.tenant, env.ent, status="published",
                           start_date=datetime(2026, 10, 11, 13, 40), end_date=datetime(2026, 10, 13, 18, 0),
                           registration_open_at=datetime(2026, 10, 11, 13, 30))
        # Bypass the ORM-side default: make_event(time_zone=None) would be re-defaulted to Asia/Kolkata on insert.
        env.db.query(Event).filter(Event.id == event.id).update({"time_zone": time_zone})
        env.db.commit()
        return event.id

    def test_null_zone_row_reads_back_verbatim_and_an_unrelated_edit_keeps_zone_and_times(self, env):
        eid = self._legacy(env, None)
        assert stored(env, eid).time_zone is None
        assert env.admin.get(f"{API}/{eid}").json()["start_date"] == "2026-10-11T13:40:00"
        assert env.admin.put(f"{API}/{eid}", json={"title": "Renamed"}).status_code == 200
        row = stored(env, eid)
        assert row.time_zone is None  # not silently "fixed" to a guessed zone
        assert (row.start_date, row.end_date, row.registration_open_at) == (
            datetime(2026, 10, 11, 13, 40), datetime(2026, 10, 13, 18, 0), datetime(2026, 10, 11, 13, 30))

    def test_invalid_stored_zone_is_not_rewritten_but_the_owner_must_correct_it_to_edit(self, env):
        eid = self._legacy(env, "Mars/Base")
        assert env.admin.get(f"{API}/{eid}").json()["time_zone"] == "Mars/Base"  # reads never mutate or hide it
        blocked = env.admin.put(f"{API}/{eid}", json={"title": "Renamed"})
        assert blocked.status_code == 400 and "time_zone" in blocked.text
        row = stored(env, eid)
        assert (row.time_zone, row.title, row.start_date) == ("Mars/Base", "Original title", datetime(2026, 10, 11, 13, 40))
        # Correcting the zone is an explicit owner action; the wall-clock values are then kept as-is.
        fixed = env.admin.put(f"{API}/{eid}", json={"time_zone": "America/New_York"})
        assert fixed.status_code == 200, fixed.text
        row = stored(env, eid)
        assert (row.time_zone, row.start_date, row.registration_open_at) == (
            "America/New_York", datetime(2026, 10, 11, 13, 40), datetime(2026, 10, 11, 13, 30))


class TestRegistrationRejectedBeforeOpeningAfterRealRoundTrip:
    def _future_window_event(self, env, suffix=""):
        now_ny = datetime.now(NY).replace(tzinfo=None, microsecond=0)
        opens, closes = now_ny + timedelta(hours=2), now_ny + timedelta(hours=5)
        fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S")
        resp = create(env, time_zone="America/New_York", pricing_type="paid",
                      ticket_types=[{"id": "general", "name": "General", "price": "500", "currency": "INR"}],
                      start_date=fmt(now_ny + timedelta(days=2)), end_date=fmt(now_ny + timedelta(days=3)),
                      registration_open_at=fmt(opens), registration_close_at=fmt(closes))
        assert resp.status_code == 201, resp.text
        eid = resp.json()["id"]
        env.db.query(Event).filter(Event.id == UUID(eid)).update({"status": "published"})
        env.db.commit()
        return eid, resp.json()

    def test_registration_quote_checkout_and_waitlist_reject_until_the_window_opens(self, env):
        eid, created = self._future_window_event(env)
        assert created["registration_open"] is False
        customer = client_for(env.db, customer_user("a@example.com"))
        person = {"participant_name": "A", "participant_email": "a@example.com"}
        calls = [
            customer.post(f"{API}/{eid}/registrations", json=person),
            customer.post(f"{API}/{eid}/checkout/quote", json={"ticket_type_id": "general", "quantity": 1}),
            customer.post(f"{API}/{eid}/checkout", json={**person, "ticket_type_id": "general"}),
            customer.post(f"{API}/{eid}/waitlist", json=person),
        ]
        for resp in calls:
            assert resp.status_code == 400 and "not yet open" in resp.json()["detail"], resp.text
