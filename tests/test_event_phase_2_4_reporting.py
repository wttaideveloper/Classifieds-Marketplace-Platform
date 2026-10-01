"""
Event Management Phase 2.4 — session attendance reporting and its integration with the Phase 2.3
dashboard and attendee views.

Real (in-memory SQLite) database via tests/event_sql_support.py: the counts are produced by the aggregate
SQL the endpoints emit (two statements per event, whatever the number of attendees).

Covers: per-session counts / percentages / multiple sessions / no attendance / empty event, the legacy
``attendance_by_session`` and ``reports?type=attendance`` fed by the dedicated records, the dashboard
``sessions`` block (and that every existing dashboard number is unchanged), ``session_attendance`` on the
attendee list and detail (and not in the CSV), and statement counts.

Run:
    pytest tests/test_event_phase_2_4_reporting.py -v
"""
import csv
import io
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event as sa_event

from event_sql_support import (
    API,
    EventRegistration,
    EventSessionAttendance,
    client_for,
    make_enterprise,
    make_event,
    make_order,
    make_registration,
    make_session,
    make_waitlist,
    paid_ticket,
    reset_overrides,
    silence_side_effects,
    staff_user,
)
from app.services.event_session_attendance_service import attendance_by_session_view, summarize_session_attendance

LONG_AGO = datetime(2020, 1, 1)


def sessions_fixture():
    return [
        {"id": "keynote", "title": "Opening Keynote", "session_date": "2026-10-05", "start_time": "09:00"},
        {"id": "workshop", "title": "AI Workshop", "session_date": "2026-10-05", "start_time": "11:00"},
        {"id": "closing", "title": "Closing Session", "session_date": "2026-10-06", "start_time": "16:00"},
    ]


@pytest.fixture
def env(monkeypatch):
    silence_side_effects(monkeypatch)
    db = make_session()
    tenant = uuid4()
    ent = make_enterprise(db, tenant)
    event = make_event(db, tenant, ent, sessions=sessions_fixture(), capacity="100")
    other = make_event(db, tenant, ent, sessions=[{"id": "keynote", "title": "Same id, other event"}])  # ids only mean something per event
    yield SimpleNamespace(db=db, tenant=tenant, ent=ent, event=event, other=other, owner=staff_user(tenant, "admin"))
    reset_overrides()
    db.close()


def client(env):
    return client_for(env.db, env.owner)


def reg(env, email=None, event=None, **kwargs):
    return make_registration(env.db, event or env.event, email or f"{uuid4().hex[:8]}@example.com", **kwargs)


def attend(env, registration, session="keynote", checked_out=False):
    row = EventSessionAttendance(event_id=registration.event_id, registration_id=registration.id, session_id=session,
                                 checked_in_at=datetime.utcnow(), checked_in_by=uuid4(),
                                 checked_out_at=datetime.utcnow() if checked_out else None)
    env.db.add(row)
    env.db.commit()
    return row


def dashboard(env, event=None):
    resp = client(env).get(f"{API}/{(event or env.event).id}/dashboard")
    assert resp.status_code == 200, resp.text
    return resp.json()


def by_id(blocks):
    return {b["session_id"]: b for b in blocks}


class Counter:
    def __init__(self, db):
        self.engine = db.get_bind()
        self.selects = 0

    def __enter__(self):
        sa_event.listen(self.engine, "before_cursor_execute", self._count)
        return self

    def __exit__(self, *exc):
        sa_event.remove(self.engine, "before_cursor_execute", self._count)

    def _count(self, conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith("SELECT"):
            self.selects += 1


# ===========================================================================
# 35-39. Counts
# ===========================================================================


class TestSessionCounts:
    def test_counts_per_session(self, env):
        people = [reg(env) for _ in range(3)]
        attend(env, people[0])
        attend(env, people[1])
        block = by_id(dashboard(env)["sessions"])["keynote"]
        assert block == {"session_id": "keynote", "title": "Opening Keynote", "session_date": "2026-10-05", "start_time": "09:00",
                         "registered_count": 3, "checked_in_count": 2, "attendance_percentage": 66.7}

    @pytest.mark.parametrize("checked_in, expected", [(0, 0.0), (1, 33.3), (2, 66.7), (3, 100.0)])
    def test_attendance_percentage(self, env, checked_in, expected):
        people = [reg(env) for _ in range(3)]
        for person in people[:checked_in]:
            attend(env, person)
        block = by_id(dashboard(env)["sessions"])["keynote"]
        assert (block["checked_in_count"], block["attendance_percentage"]) == (checked_in, expected)

    def test_multiple_sessions_are_counted_independently(self, env):
        people = [reg(env) for _ in range(4)]
        for person in people:
            attend(env, person, "keynote")
        attend(env, people[0], "workshop")
        attend(env, people[1], "workshop")
        attend(env, people[1], "closing")
        blocks = by_id(dashboard(env)["sessions"])
        assert [blocks[s]["checked_in_count"] for s in ("keynote", "workshop", "closing")] == [4, 2, 1]
        assert [blocks[s]["attendance_percentage"] for s in ("keynote", "workshop", "closing")] == [100.0, 50.0, 25.0]
        assert {b["registered_count"] for b in blocks.values()} == {4}

    def test_no_attendance_at_all(self, env):
        for _ in range(2):
            reg(env)
        blocks = dashboard(env)["sessions"]
        assert [(b["checked_in_count"], b["attendance_percentage"]) for b in blocks] == [(0, 0.0)] * 3

    def test_no_registrations_gives_a_null_percentage_not_a_division_error(self, env):
        blocks = dashboard(env)["sessions"]
        assert [(b["registered_count"], b["checked_in_count"], b["attendance_percentage"]) for b in blocks] == [(0, 0, None)] * 3

    def test_an_event_without_sessions_reports_none(self, env):
        bare = make_event(env.db, env.tenant, env.ent, sessions=[])
        assert dashboard(env, bare)["sessions"] == []
        body = client(env).get(f"{API}/{bare.id}/attendance").json()
        assert body["attendance_by_session"] is None
        report = client(env).get(f"{API}/{bare.id}/reports", params={"type": "attendance"}).json()
        assert report["data"]["by_session"] == {}

    def test_sessions_follow_the_events_own_order_and_carry_display_fields(self, env):
        blocks = dashboard(env)["sessions"]
        assert [b["session_id"] for b in blocks] == ["keynote", "workshop", "closing"]
        assert [b["title"] for b in blocks] == ["Opening Keynote", "AI Workshop", "Closing Session"]
        assert [b["start_time"] for b in blocks] == ["09:00", "11:00", "16:00"]

    def test_only_attendees_who_still_hold_a_registration_count(self, env):
        a, b, c = reg(env), reg(env), reg(env, status="no_show")
        for person in (a, b, c):
            attend(env, person)
        assert by_id(dashboard(env)["sessions"])["keynote"]["checked_in_count"] == 2  # the no_show is not counted (nor registered)
        env.db.query(EventRegistration).filter_by(id=b.id).update({"status": "cancelled"})
        env.db.commit()
        block = by_id(dashboard(env)["sessions"])["keynote"]
        assert (block["registered_count"], block["checked_in_count"], block["attendance_percentage"]) == (1, 1, 100.0)  # never above 100

    def test_event_level_attended_registrations_count_as_registered(self, env):
        a = reg(env, status="attended", checked_in_at=datetime(2026, 3, 1, 9, 0))
        reg(env)
        attend(env, a)
        block = by_id(dashboard(env)["sessions"])["keynote"]
        assert (block["registered_count"], block["checked_in_count"]) == (2, 1)

    def test_attendance_in_another_event_with_the_same_session_id_is_not_counted(self, env):
        mine, theirs = reg(env), reg(env, event=env.other)
        attend(env, mine, "keynote")
        attend(env, theirs, "keynote")
        assert by_id(dashboard(env)["sessions"])["keynote"]["checked_in_count"] == 1
        assert dashboard(env, env.other)["sessions"][0]["checked_in_count"] == 1

    def test_a_deleted_session_is_no_longer_reported_but_its_history_is_kept(self, env):
        a = reg(env)
        attend(env, a, "keynote")
        assert client(env).delete(f"{API}/{env.event.id}/sessions/keynote").status_code == 200
        assert [b["session_id"] for b in dashboard(env)["sessions"]] == ["workshop", "closing"]
        assert env.db.query(EventSessionAttendance).count() == 1

    def test_a_duplicated_session_id_is_reported_once_everywhere(self, env):
        dup = make_event(env.db, env.tenant, env.ent, sessions=[
            {"id": "same", "title": "First"}, {"id": "same", "title": "Second"}, {"id": "other", "title": "Other"}])
        person = reg(env, "a@example.com", event=dup)
        attend(env, person, "same")
        blocks = dashboard(env, dup)["sessions"]
        assert [(b["session_id"], b["title"], b["checked_in_count"]) for b in blocks] == [("same", "First", 1), ("other", "Other", 0)]
        item = client(env).get(f"{API}/{dup.id}/attendees").json()["items"][0]
        assert [(s["session_id"], s["title"], s["checked_in"]) for s in item["session_attendance"]] == [("same", "First", True), ("other", "Other", False)]
        assert list(client(env).get(f"{API}/{dup.id}/attendance").json()["attendance_by_session"]) == ["same", "other"]

    def test_legacy_id_less_sessions_are_left_out(self, env):
        legacy = make_event(env.db, env.tenant, env.ent, sessions=[{"title": "No id"}, {"id": "ok", "title": "Has id"}])
        assert [b["session_id"] for b in dashboard(env, legacy)["sessions"]] == ["ok"]


# ===========================================================================
# The legacy report fields now come from the dedicated records
# ===========================================================================


class TestAttendanceReport:
    def test_attendance_by_session_is_fed_by_session_attendance(self, env):
        people = [reg(env) for _ in range(4)]
        attend(env, people[0], "keynote")
        attend(env, people[1], "keynote")
        attend(env, people[2], "workshop")
        body = client(env).get(f"{API}/{env.event.id}/attendance").json()
        assert body["attendance_by_session"] == {
            "keynote": {"session_id": "keynote", "title": "Opening Keynote", "total": 4, "attended": 2, "attendance_percentage": 50.0},
            "workshop": {"session_id": "workshop", "title": "AI Workshop", "total": 4, "attended": 1, "attendance_percentage": 25.0},
            "closing": {"session_id": "closing", "title": "Closing Session", "total": 4, "attended": 0, "attendance_percentage": 0.0},
        }
        # the rest of the report is the event-level report, unchanged
        assert (body["total_registered"], body["total_attended"], body["total_no_show"]) == (4, 0, 0)
        assert len(body["participants"]) == 4

    def test_registration_session_id_is_no_longer_the_source(self, env):
        r = reg(env)
        resp = client(env).post(f"{API}/{env.event.id}/check-in", json={"registration_id": str(r.id), "session_id": "keynote"})
        assert resp.status_code == 200  # legacy event-level check-in that names a session still works ...
        body = client(env).get(f"{API}/{env.event.id}/attendance").json()
        assert body["total_attended"] == 1 and body["participants"][0]["session_id"] == "keynote"  # ... and is reported as before
        assert body["attendance_by_session"]["keynote"]["attended"] == 0  # ... but it is not session attendance

    def test_reports_attendance_type_uses_the_same_numbers(self, env):
        people = [reg(env) for _ in range(2)]
        attend(env, people[0], "closing")
        data = client(env).get(f"{API}/{env.event.id}/reports", params={"type": "attendance"}).json()["data"]
        assert data["by_session"]["closing"] == {"session_id": "closing", "title": "Closing Session", "total": 2, "attended": 1, "attendance_percentage": 50.0}
        assert (data["total"], data["attended"], data["no_show"], data["cancelled"]) == (2, 0, 0, 0)

    def test_other_reports_are_unchanged(self, env):
        reg(env)
        data = client(env).get(f"{API}/{env.event.id}/reports", params={"type": "registration"}).json()["data"]
        assert data == {"total_registrations": 1, "by_status": {"confirmed": 1}, "by_ticket_type": {}}

    def test_the_report_routes_keep_their_security(self, env):
        assert client_for(env.db, staff_user(uuid4(), "admin")).get(f"{API}/{env.event.id}/attendance").status_code == 403
        assert client_for(env.db, None).get(f"{API}/{env.event.id}/attendance").status_code == 401

    def test_view_and_summary_helpers(self, env):
        assert attendance_by_session_view([]) is None
        reg(env)
        summaries = summarize_session_attendance(env.db, env.event)
        assert [s.session_id for s in summaries] == ["keynote", "workshop", "closing"]
        assert attendance_by_session_view(summaries)["keynote"]["total"] == 1
        bare = make_event(env.db, env.tenant, env.ent, sessions=[])
        assert summarize_session_attendance(env.db, bare) == []


# ===========================================================================
# 40-41. Dashboard
# ===========================================================================


class TestDashboard:
    def paid_world(self, env):
        ticket = paid_ticket("500", ticket_id="general")
        event = make_event(env.db, env.tenant, env.ent, pricing_type="paid", price="500", ticket_types=[ticket], capacity="10",
                           sessions=sessions_fixture())
        people = []
        for i, quantity in enumerate(("1", "3", "1")):
            email = f"buyer{i}@example.com"
            make_order(env.db, event, email, ticket_type_id="general", quantity=quantity, amount=str(500 * int(quantity)), created_at=LONG_AGO)
            people.append(reg(env, email, event=event, ticket_type_id="general"))
        people.append(reg(env, "checked-in@example.com", event=event, status="attended", checked_in_at=datetime(2026, 3, 1, 9, 0)))
        make_waitlist(env.db, event, "w1@example.com", status="waiting")
        make_waitlist(env.db, event, "w2@example.com", status="payment_pending", expires_at=datetime.utcnow() + timedelta(minutes=10))
        return event, people

    def test_every_existing_dashboard_number_is_unchanged_by_session_attendance(self, env):
        event, people = self.paid_world(env)
        before = dashboard(env, event)
        for person in people:
            for session in ("keynote", "workshop"):
                attend(env, person, session)
        after = dashboard(env, event)
        for key in ("event", "registrations", "capacity", "attendance", "waitlist", "orders", "revenue"):
            assert after[key] == before[key], key
        assert after["attendance"]["checked_in"] == 1  # event-level check-in only: the one attended registration
        assert before["sessions"] != after["sessions"]

    def test_the_phase_2_3_numbers_are_still_right(self, env):
        event, _ = self.paid_world(env)
        body = dashboard(env, event)
        assert body["registrations"] == {"total": 4, "active": 4, "confirmed": 3, "attended": 1, "cancelled": 0, "no_show": 0, "other": 0,
                                          "online": 4, "walk_in": 0}
        assert body["capacity"]["seats_taken"] == 6 and body["capacity"]["seats_reserved"] == 1 and body["capacity"]["available_seats"] == 3
        assert body["orders"]["successful"] == 3 and body["revenue"]["total_revenue"] == 2500.0
        assert body["waitlist"]["waiting"] == 1 and body["waitlist"]["payment_pending"] == 1

    def test_the_sessions_block_is_typed_and_bounded(self, env):
        reg(env)
        block = dashboard(env)["sessions"][0]
        assert set(block) == {"session_id", "title", "session_date", "start_time", "registered_count", "checked_in_count", "attendance_percentage"}

    def test_statement_count_is_constant_as_attendance_grows(self, env):
        def selects():
            with Counter(env.db) as counter:
                assert client(env).get(f"{API}/{env.event.id}/dashboard").status_code == 200
            return counter.selects

        for _ in range(3):
            person = reg(env)
            attend(env, person, "keynote")
        few = selects()
        for _ in range(60):
            person = reg(env)
            attend(env, person, "keynote")
            attend(env, person, "workshop")
        assert selects() == few
        bare = make_event(env.db, env.tenant, env.ent, sessions=[])
        with Counter(env.db) as counter:
            client(env).get(f"{API}/{bare.id}/dashboard")
        assert few - counter.selects == 2  # the session block costs two aggregate statements, and none without sessions

    def test_the_attendance_report_statement_count_is_constant_too(self, env):
        def selects():
            with Counter(env.db) as counter:
                assert client(env).get(f"{API}/{env.event.id}/attendance").status_code == 200
            return counter.selects

        for _ in range(3):
            attend(env, reg(env))
        few = selects()
        for _ in range(40):
            attend(env, reg(env), "closing")
        # the participants list is loaded as before; only the session block must not grow with attendance rows
        assert selects() == few

    def test_dashboard_security_is_unchanged(self, env):
        assert client_for(env.db, staff_user(uuid4(), "admin")).get(f"{API}/{env.event.id}/dashboard").status_code == 403


# ===========================================================================
# Attendee list / detail
# ===========================================================================


class TestAttendeeIntegration:
    def list(self, env, event=None, **params):
        resp = client(env).get(f"{API}/{(event or env.event).id}/attendees", params={"page_size": 100, **params})
        assert resp.status_code == 200, resp.text
        return {item["participant_email"]: item for item in resp.json()["items"]}

    def test_each_attendee_shows_every_session_as_attended_or_not(self, env):
        a = reg(env, "a@example.com")
        reg(env, "b@example.com")
        attend(env, a, "keynote")
        attend(env, a, "workshop", checked_out=True)
        items = self.list(env)
        states = {s["session_id"]: s for s in items["a@example.com"]["session_attendance"]}
        assert list(states) == ["keynote", "workshop", "closing"]  # the event's own order
        assert (states["keynote"]["checked_in"], states["workshop"]["checked_in"], states["closing"]["checked_in"]) == (True, True, False)
        assert states["keynote"]["title"] == "Opening Keynote" and states["keynote"]["checked_in_at"] and states["keynote"]["checked_in_by"]
        assert states["keynote"]["checked_out_at"] is None and states["workshop"]["checked_out_at"] is not None
        assert states["closing"]["checked_in_at"] is None and states["closing"]["checked_in_by"] is None
        assert [s["checked_in"] for s in items["b@example.com"]["session_attendance"]] == [False, False, False]

    def test_session_attendance_is_separate_from_event_level_check_in(self, env):
        a = reg(env, "a@example.com")
        attend(env, a, "keynote")
        item = self.list(env)["a@example.com"]
        assert item["is_checked_in"] is False and item["registration_status"] == "confirmed" and item["checked_in_at"] is None
        reg(env, "b@example.com", status="attended", checked_in_at=datetime(2026, 3, 1, 9, 0))
        assert self.list(env)["b@example.com"]["is_checked_in"] is True
        assert [s["checked_in"] for s in self.list(env)["b@example.com"]["session_attendance"]] == [False, False, False]

    def test_detail_matches_the_list(self, env):
        a = reg(env, "a@example.com")
        attend(env, a, "closing")
        detail = client(env).get(f"{API}/{env.event.id}/registrations/{a.id}").json()
        assert detail == self.list(env)["a@example.com"]
        assert [s["checked_in"] for s in detail["session_attendance"]] == [False, False, True]

    def test_state_shape_exposes_no_tenant_or_meeting_internals(self, env):
        a = reg(env, "a@example.com")
        attend(env, a)
        state = self.list(env)["a@example.com"]["session_attendance"][0]
        assert set(state) == {"session_id", "title", "checked_in", "checked_in_at", "checked_in_by", "checked_out_at"}

    def test_deleted_and_legacy_sessions_are_not_listed(self, env):
        a = reg(env, "a@example.com")
        attend(env, a, "keynote")
        client(env).delete(f"{API}/{env.event.id}/sessions/keynote")
        assert [s["session_id"] for s in self.list(env)["a@example.com"]["session_attendance"]] == ["workshop", "closing"]
        legacy = make_event(env.db, env.tenant, env.ent, sessions=[{"title": "No id"}])
        reg(env, "l@example.com", event=legacy)
        assert self.list(env, legacy)["l@example.com"]["session_attendance"] == []

    def test_an_event_without_sessions_has_an_empty_list_and_no_extra_query(self, env):
        bare = make_event(env.db, env.tenant, env.ent, sessions=[])
        reg(env, "a@example.com", event=bare)
        assert self.list(env, bare)["a@example.com"]["session_attendance"] == []

        def selects(event):
            with Counter(env.db) as counter:
                client(env).get(f"{API}/{event.id}/attendees")
            return counter.selects

        assert selects(env.event) - selects(bare) == 1  # one page-wide attendance query, only when the event has sessions

    def test_another_events_attendance_never_leaks_into_an_attendee(self, env):
        mine = reg(env, "same@example.com")
        theirs = reg(env, "same@example.com", event=env.other)
        attend(env, theirs, "keynote")  # same session id on the other event
        assert [s["checked_in"] for s in self.list(env)["same@example.com"]["session_attendance"]] == [False, False, False]
        assert mine.id != theirs.id

    def test_list_statement_count_is_constant_with_many_attendees(self, env):
        def selects():
            with Counter(env.db) as counter:
                assert client(env).get(f"{API}/{env.event.id}/attendees", params={"page_size": 100}).status_code == 200
            return counter.selects

        for _ in range(3):
            attend(env, reg(env))
        few = selects()
        for _ in range(60):
            attend(env, reg(env), "workshop")
        assert selects() == few

    def test_the_csv_export_columns_are_stable_with_new_ones_only_appended(self, env):
        a = reg(env, "a@example.com")
        attend(env, a)
        resp = client(env).get(f"{API}/{env.event.id}/registrations/export")
        header = next(csv.reader(io.StringIO(resp.text)))
        # The original 18 columns keep their exact name/order (Phase 2.8 only appends after them).
        assert header[:18] == ["id", "name", "email", "status", "qr_code", "ticket_type", "quantity", "payment_status", "order_id",
                          "amount", "currency", "checked_in", "checked_in_at", "registered_at", "answers", "source", "meals", "accommodation"]
        assert header[18:] == ["meal_subtotal", "accommodation_subtotal"]
