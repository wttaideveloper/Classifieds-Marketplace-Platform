"""
Event Management Phase 2.4 — session-level attendance (check-in / undo / check-out / batch).

Real (in-memory SQLite) database via tests/event_sql_support.py, so the unique constraint, the savepoint
handling of a duplicate, the bulk batch queries and the transactional audit rows run as SQL.

Covers: session / registration validation (cross-event, legacy id-less sessions, cancelled, refunded,
event states), check-in idempotency and operator/timestamp recording, undo, check-out, independent
multi-session attendance, the security matrix, QR-based operation, batch (per-entry validation, races,
constant statement count), audit rows and transactionality, compatibility with event-level check-in, and
OpenAPI.

Run:
    pytest tests/test_event_phase_2_4_session_attendance.py -v
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import event as sa_event
from sqlalchemy.exc import IntegrityError

from event_sql_support import (
    API,
    Event,
    EventAudit,
    EventOrder,
    EventRegistration,
    EventSessionAttendance,
    EventWaitlist,
    client_for,
    customer_user,
    make_enterprise,
    make_event,
    make_order,
    make_registration,
    make_session,
    make_waitlist,
    reset_overrides,
    silence_side_effects,
    staff_user,
    super_admin_user,
)
import app.services.event_session_attendance_service as svc

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
    event = make_event(db, tenant, ent, sessions=sessions_fixture())
    other = make_event(db, tenant, ent, sessions=[{"id": "other-session", "title": "Elsewhere"}])
    owner = staff_user(tenant, "admin")
    yield SimpleNamespace(db=db, tenant=tenant, ent=ent, event=event, other=other, owner=owner)
    reset_overrides()
    db.close()


# ---------------------------------------------------------------------------- helpers


def owner_client(env):
    return client_for(env.db, env.owner)


def url(env, session, action, event=None):
    return f"{API}/{(event or env.event).id}/sessions/{session}/{action}"


def post(env, session, action, body, client=None, event=None):
    return (client or owner_client(env)).post(url(env, session, action, event), json=body)


def reg(env, email=None, event=None, **kwargs):
    return make_registration(env.db, event or env.event, email or f"{uuid4().hex[:8]}@example.com", **kwargs)


def check_in(env, registration, session="keynote", **kwargs):
    return post(env, session, "check-in", {"registration_id": str(registration.id)}, **kwargs)


def rows(env, **filters):
    env.db.expire_all()
    return env.db.query(EventSessionAttendance).filter_by(**filters).all()


def audits(env, action=None):
    env.db.expire_all()
    query = env.db.query(EventAudit)
    if action:
        query = query.filter(EventAudit.action == action)
    return query.order_by(EventAudit.created_at).all()


def add_row(env, registration, session="keynote", **kwargs):
    row = EventSessionAttendance(event_id=registration.event_id, registration_id=registration.id, session_id=session,
                                 checked_in_at=kwargs.pop("checked_in_at", datetime.utcnow()), **kwargs)
    env.db.add(row)
    env.db.commit()
    return row


def snapshot(db):
    """Everything session attendance must NOT touch."""
    db.expire_all()
    return (
        sorted((str(r.id), r.status, r.checked_in_at, r.checked_in_by, r.checked_out_at, r.session_id) for r in db.query(EventRegistration)),
        sorted((str(o.id), o.status, o.payment_status, o.amount, o.quantity) for o in db.query(EventOrder)),
        sorted((str(w.id), w.status, w.payment_offer_expires_at) for w in db.query(EventWaitlist)),
        sorted((str(e.id), e.status, str(e.sessions), e.capacity, e.pricing_type) for e in db.query(Event)),
    )


def count_commits(db):
    calls = []
    original = db.commit
    db.commit = lambda: (calls.append(1), original())[1]
    return calls


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
# 1-7. Validation
# ===========================================================================


class TestValidation:
    def test_valid_session_check_in(self, env):
        r = reg(env)
        resp = check_in(env, r)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["outcome"] == "checked_in" and body["checked_in"] is True
        assert body["session_id"] == "keynote" and body["session_title"] == "Opening Keynote"
        assert body["event_id"] == str(env.event.id) and body["registration_id"] == str(r.id)
        assert body["registration_status"] == "confirmed"  # session attendance does not touch it

    @pytest.mark.parametrize("session", ["nope", "None", "null", "undefined", "KEYNOTE", "keynote ", "other-session"])
    def test_a_session_that_is_not_one_of_this_events_sessions_is_404(self, env, session):
        r = reg(env)
        resp = check_in(env, r, session=session)
        assert resp.status_code == 404 and resp.json()["detail"] == "Session not found"
        assert rows(env) == [] and audits(env) == []

    def test_a_session_of_another_event_cannot_be_used_even_by_the_same_tenant(self, env):
        r = reg(env)
        assert check_in(env, r, session="other-session").status_code == 404  # exists, but on env.other
        assert rows(env) == []
        # ... and it works on the event that owns it, for that event's own registration
        other_reg = reg(env, event=env.other)
        assert check_in(env, other_reg, session="other-session", event=env.other).status_code == 200

    def test_a_registration_of_another_event_is_404(self, env):
        foreign = reg(env, event=env.other)
        resp = check_in(env, foreign)
        assert resp.status_code == 404 and resp.json()["detail"] == "Registration not found"
        assert rows(env) == []

    def test_unknown_registration_id_and_unknown_qr_are_404(self, env):
        assert post(env, "keynote", "check-in", {"registration_id": str(uuid4())}).status_code == 404
        assert post(env, "keynote", "check-in", {"qr_code": "NOSUCHQR"}).status_code == 404

    def test_neither_registration_id_nor_qr_is_400(self, env):
        assert post(env, "keynote", "check-in", {}).status_code == 400
        assert post(env, "keynote", "check-in", {"qr_code": ""}).status_code == 400

    def test_cancelled_registration_is_rejected(self, env):
        r = reg(env, status="cancelled")
        resp = check_in(env, r)
        assert resp.status_code == 400 and "cancelled" in resp.json()["detail"]
        assert rows(env) == []

    def test_refunded_registration_is_rejected_even_if_it_is_still_marked_confirmed(self, env):
        r = reg(env, "paid@example.com")
        make_order(env.db, env.event, "paid@example.com", status="refunded", payment_status="refunded", created_at=LONG_AGO)
        resp = check_in(env, r)
        assert resp.status_code == 400 and "refunded" in resp.json()["detail"]
        assert rows(env) == []

    def test_a_refund_approved_through_the_real_flow_blocks_session_check_in(self, env):
        r = reg(env, "buyer@example.com")
        order = make_order(env.db, env.event, "buyer@example.com", status="refund_requested", payment_status="refund_requested", created_at=LONG_AGO)
        approve = owner_client(env).post(f"{API}/{env.event.id}/orders/{order.id}/refund/approve", json={"action": "approve"})
        assert approve.status_code == 200, approve.text
        assert check_in(env, r).status_code == 400  # approval cancelled the registration

    def test_a_pending_refund_request_alone_does_not_block(self, env):
        r = reg(env, "asked@example.com")
        make_order(env.db, env.event, "asked@example.com", status="refund_requested", payment_status="refund_requested", created_at=LONG_AGO)
        assert check_in(env, r).status_code == 200  # same as event-level: only a completed refund blocks

    def test_malformed_ids_are_422(self, env):
        assert owner_client(env).post(f"{API}/not-a-uuid/sessions/keynote/check-in", json={"qr_code": "X"}).status_code == 422
        assert post(env, "keynote", "check-in", {"registration_id": "not-a-uuid"}).status_code == 422
        assert owner_client(env).post(url(env, "x" * 101, "check-in"), json={"qr_code": "X"}).status_code == 422


class TestLegacySessionsAndSessionIds:
    def test_id_less_legacy_sessions_cannot_take_attendance_and_are_never_mutated(self, env):
        legacy = make_event(env.db, env.tenant, env.ent, sessions=[{"title": "Legacy talk"}, {"id": "modern", "title": "Modern talk"}])
        before = [dict(s) for s in legacy.sessions]
        r = reg(env, event=legacy)
        for guess in ("Legacy talk", "None", "0", "null"):
            assert post(env, guess, "check-in", {"registration_id": str(r.id)}, event=legacy).status_code == 404
        assert post(env, "modern", "check-in", {"registration_id": str(r.id)}, event=legacy).status_code == 200
        env.db.expire_all()
        assert [dict(s) for s in env.db.get(Event, legacy.id).sessions] == before  # no write-on-read, no id invented

    def test_a_legacy_session_becomes_usable_once_an_existing_session_write_gave_it_an_id(self, env):
        legacy = make_event(env.db, env.tenant, env.ent, sessions=[{"title": "Legacy talk", "session_date": env.event.start_date.date().isoformat()}])
        client = owner_client(env)
        added = client.post(f"{API}/{legacy.id}/sessions", json={"session_date": env.event.start_date.date().isoformat(), "title": "Added"})
        assert added.status_code == 201, added.text
        env.db.expire_all()
        by_title = {s["title"]: s["id"] for s in env.db.get(Event, legacy.id).sessions}
        assert set(by_title) == {"Legacy talk", "Added"}
        r = reg(env, event=legacy)
        assert post(env, by_title["Legacy talk"], "check-in", {"registration_id": str(r.id)}, event=legacy).status_code == 200
        # the id is stable across further session edits
        client.put(f"{API}/{legacy.id}/sessions/{by_title['Added']}", json={"title": "Renamed"})
        env.db.expire_all()
        assert {s["title"]: s["id"] for s in env.db.get(Event, legacy.id).sessions}["Legacy talk"] == by_title["Legacy talk"]

    def test_a_session_added_through_the_existing_endpoint_can_take_attendance(self, env):
        added = owner_client(env).post(f"{API}/{env.event.id}/sessions",
                                       json={"session_date": env.event.start_date.date().isoformat(), "title": "Late addition"})
        assert added.status_code == 201, added.text
        r = reg(env)
        assert check_in(env, r, session=added.json()["id"]).status_code == 200

    def test_duplicate_session_ids_resolve_to_the_first(self, env):
        dup = make_event(env.db, env.tenant, env.ent, sessions=[{"id": "same", "title": "First"}, {"id": "same", "title": "Second"}])
        r = reg(env, event=dup)
        body = post(env, "same", "check-in", {"registration_id": str(r.id)}, event=dup).json()
        assert body["session_title"] == "First"

    def test_deleting_a_session_keeps_history_but_closes_it_to_new_attendance(self, env):
        r, r2 = reg(env), reg(env)
        assert check_in(env, r).status_code == 200
        assert owner_client(env).delete(f"{API}/{env.event.id}/sessions/keynote").status_code == 200
        assert check_in(env, r2).status_code == 404
        assert [(x.registration_id, x.session_id) for x in rows(env)] == [(r.id, "keynote")]  # kept, not reported


# ===========================================================================
# Event states / eligibility parity with event-level check-in
# ===========================================================================


class TestEligibilityMirrorsEventLevelCheckIn:
    @pytest.mark.parametrize("event_status", ["draft", "pending_approval", "approved", "published", "cancelled", "completed", "archived", "suspended"])
    def test_event_states(self, env, event_status):
        event = make_event(env.db, env.tenant, env.ent, status=event_status, sessions=sessions_fixture())
        r = reg(env, event=event)
        client = owner_client(env)
        event_level = client.post(f"{API}/{event.id}/check-in", json={"registration_id": str(r.id)}).status_code
        session_level = post(env, "keynote", "check-in", {"registration_id": str(r.id)}, event=event).status_code
        assert session_level == event_level
        assert (session_level == 400) == (event_status in svc.CHECK_IN_BLOCKED_EVENT_STATUSES)

    @pytest.mark.parametrize("status_", ["confirmed", "attended", "no_show", "cancelled"])
    def test_registration_states(self, env, status_):
        event_level_reg = reg(env, status=status_)
        session_reg = reg(env, status=status_)
        event_level = owner_client(env).post(f"{API}/{env.event.id}/check-in", json={"registration_id": str(event_level_reg.id)}).status_code
        session_level = check_in(env, session_reg).status_code
        assert session_level == event_level

    def test_refunded_parity(self, env):
        a, b = reg(env, "a@example.com"), reg(env, "b@example.com")
        for email in ("a@example.com", "b@example.com"):
            make_order(env.db, env.event, email, status="refunded", payment_status="refunded", created_at=LONG_AGO)
        event_level = owner_client(env).post(f"{API}/{env.event.id}/check-in", json={"registration_id": str(a.id)}).status_code
        assert event_level == check_in(env, b).status_code == 400

    def test_being_checked_in_at_event_level_is_neither_required_nor_changed(self, env):
        not_arrived = reg(env)
        assert check_in(env, not_arrived).status_code == 200  # no event-level check-in needed
        arrived = reg(env, status="attended", checked_in_at=datetime(2026, 3, 1, 9, 0))
        assert check_in(env, arrived).status_code == 200


# ===========================================================================
# 8-12. Check-in
# ===========================================================================


class TestCheckIn:
    def test_first_check_in_creates_the_attendance_row(self, env):
        r = reg(env)
        assert check_in(env, r).status_code == 200
        (row,) = rows(env)
        assert (row.event_id, row.registration_id, row.session_id) == (env.event.id, r.id, "keynote")
        assert row.checked_out_at is None and row.checked_out_by is None

    def test_duplicate_check_in_is_idempotent(self, env):
        r = reg(env)
        first = check_in(env, r).json()
        second = check_in(env, r)
        assert second.status_code == 200
        body = second.json()
        assert body["outcome"] == "already_checked_in" and body["checked_in"] is True
        assert body["checked_in_at"] == first["checked_in_at"] and body["checked_in_by"] == first["checked_in_by"]  # original kept
        assert len(rows(env)) == 1
        assert len(audits(env, "session_check_in")) == 1  # a repeat scan writes no audit row

    def test_the_operator_is_recorded(self, env):
        r = reg(env)
        body = check_in(env, r).json()
        assert body["checked_in_by"] == env.owner["id"]
        assert str(rows(env)[0].checked_in_by) == env.owner["id"]

    def test_a_different_staff_member_recorded_on_a_different_session(self, env):
        provider = staff_user(env.tenant, "provider")
        r = reg(env)
        check_in(env, r, session="keynote")
        check_in(env, r, session="workshop", client=client_for(env.db, provider))
        by_session = {x.session_id: str(x.checked_in_by) for x in rows(env)}
        assert by_session == {"keynote": env.owner["id"], "workshop": provider["id"]}

    def test_an_operator_id_that_is_not_a_uuid_is_recorded_as_null_not_an_error(self, env):
        odd = {**env.owner, "id": "not-a-uuid"}
        r = reg(env)
        resp = check_in(env, r, client=client_for(env.db, odd))
        assert resp.status_code == 200 and resp.json()["checked_in_by"] is None

    def test_the_timestamp_is_recorded(self, env):
        r = reg(env)
        before = datetime.utcnow() - timedelta(seconds=2)
        body = check_in(env, r).json()
        stamped = datetime.fromisoformat(body["checked_in_at"])
        assert before <= stamped <= datetime.utcnow() + timedelta(seconds=2)
        assert rows(env)[0].checked_in_at == stamped
        assert rows(env)[0].created_at is not None and rows(env)[0].updated_at is not None

    def test_check_in_by_qr(self, env):
        r = reg(env)
        resp = post(env, "keynote", "check-in", {"qr_code": r.qr_code, "method": "qr_code"})
        assert resp.status_code == 200 and resp.json()["registration_id"] == str(r.id)

    def test_registration_id_wins_over_qr_when_both_are_sent(self, env):
        a, b = reg(env), reg(env)
        resp = post(env, "keynote", "check-in", {"registration_id": str(a.id), "qr_code": b.qr_code})
        assert resp.json()["registration_id"] == str(a.id)

    def test_response_shape_is_typed(self, env):
        body = check_in(env, reg(env)).json()
        assert set(body) == {"message", "outcome", "event_id", "session_id", "session_title", "registration_id", "participant_name",
                             "participant_email", "registration_status", "checked_in", "checked_in_at", "checked_in_by",
                             "checked_out_at", "checked_out_by"}


# ===========================================================================
# 13-15. Undo
# ===========================================================================


class TestUncheckIn:
    def test_valid_uncheck_in_removes_the_attendance(self, env):
        r = reg(env)
        check_in(env, r)
        resp = post(env, "keynote", "uncheck-in", {"registration_id": str(r.id), "reason": "wrong badge"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["outcome"] == "unchecked_in" and body["checked_in"] is False
        assert body["checked_in_at"] is None and body["checked_in_by"] is None and body["checked_out_at"] is None
        assert rows(env) == []

    def test_uncheck_clears_the_state_so_a_new_check_in_is_fresh(self, env):
        r = reg(env)
        check_in(env, r)
        post(env, "keynote", "check-out", {"registration_id": str(r.id)})
        post(env, "keynote", "uncheck-in", {"registration_id": str(r.id)})
        again = check_in(env, r).json()
        assert again["outcome"] == "checked_in" and again["checked_out_at"] is None  # the check-out did not survive the undo
        assert len(rows(env)) == 1

    def test_uncheck_of_a_session_they_never_attended_is_400(self, env):
        r = reg(env)
        resp = post(env, "keynote", "uncheck-in", {"registration_id": str(r.id)})
        assert resp.status_code == 400 and "not checked in" in resp.json()["detail"]
        assert audits(env, "session_uncheck_in") == []

    def test_uncheck_only_affects_the_named_session(self, env):
        r = reg(env)
        check_in(env, r, "keynote")
        check_in(env, r, "workshop")
        post(env, "keynote", "uncheck-in", {"registration_id": str(r.id)})
        assert [x.session_id for x in rows(env)] == ["workshop"]

    def test_uncheck_by_qr(self, env):
        r = reg(env)
        check_in(env, r)
        assert post(env, "keynote", "uncheck-in", {"qr_code": r.qr_code}).status_code == 200
        assert rows(env) == []

    def test_uncheck_validates_session_and_registration_like_check_in(self, env):
        r, foreign = reg(env), reg(env, event=env.other)
        add_row(env, foreign, "other-session")
        assert post(env, "nope", "uncheck-in", {"registration_id": str(r.id)}).status_code == 404
        assert post(env, "keynote", "uncheck-in", {"registration_id": str(foreign.id)}).status_code == 404
        assert len(rows(env)) == 1  # the other event's attendance is untouched

    def test_a_cancelled_registration_can_still_have_its_attendance_removed(self, env):
        r = reg(env)
        check_in(env, r)
        env.db.query(EventRegistration).filter_by(id=r.id).update({"status": "cancelled"})
        env.db.commit()
        assert post(env, "keynote", "uncheck-in", {"registration_id": str(r.id)}).status_code == 200  # a correction, not a check-in

    def test_event_level_check_in_is_untouched_by_a_session_undo(self, env):
        r = reg(env)
        assert owner_client(env).post(f"{API}/{env.event.id}/check-in", json={"registration_id": str(r.id)}).status_code == 200
        event_level = snapshot(env.db)[0]
        check_in(env, r)
        post(env, "keynote", "uncheck-in", {"registration_id": str(r.id)})
        assert snapshot(env.db)[0] == event_level
        env.db.expire_all()
        fresh = env.db.get(EventRegistration, r.id)
        assert fresh.status == "attended" and fresh.checked_in_at is not None


# ===========================================================================
# Check-out
# ===========================================================================


class TestCheckOut:
    def test_check_out_records_time_and_operator(self, env):
        r = reg(env)
        check_in(env, r)
        provider = staff_user(env.tenant, "provider")
        resp = post(env, "keynote", "check-out", {"registration_id": str(r.id)}, client=client_for(env.db, provider))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["outcome"] == "checked_out" and body["checked_in"] is True and body["checked_out_at"] is not None
        assert body["checked_out_by"] == provider["id"] and body["checked_in_by"] == env.owner["id"]
        assert rows(env)[0].checked_out_at is not None

    def test_check_out_needs_a_check_in_to_that_session(self, env):
        r = reg(env)
        check_in(env, r, "workshop")
        resp = post(env, "keynote", "check-out", {"registration_id": str(r.id)})
        assert resp.status_code == 400 and "not been checked in" in resp.json()["detail"]

    def test_repeat_check_out_is_idempotent(self, env):
        r = reg(env)
        check_in(env, r)
        first = post(env, "keynote", "check-out", {"registration_id": str(r.id)}).json()
        second = post(env, "keynote", "check-out", {"qr_code": r.qr_code})
        assert second.status_code == 200 and second.json()["outcome"] == "already_checked_out"
        assert second.json()["checked_out_at"] == first["checked_out_at"]
        assert len(audits(env, "session_check_out")) == 1

    def test_check_in_after_check_out_does_not_reopen_the_session(self, env):
        r = reg(env)
        check_in(env, r)
        post(env, "keynote", "check-out", {"registration_id": str(r.id)})
        body = check_in(env, r).json()
        assert body["outcome"] == "already_checked_in" and body["checked_out_at"] is not None  # undo first, like event-level

    @pytest.mark.parametrize("status_", ["cancelled", "no_show"])
    def test_check_out_of_a_cancelled_or_no_show_registration_is_400(self, env, status_):
        r = reg(env)
        check_in(env, r)
        env.db.query(EventRegistration).filter_by(id=r.id).update({"status": status_})
        env.db.commit()
        assert post(env, "keynote", "check-out", {"registration_id": str(r.id)}).status_code == 400

    def test_check_out_does_not_touch_event_level_check_out(self, env):
        r = reg(env, status="attended", checked_in_at=datetime(2026, 3, 1, 9, 0))
        check_in(env, r)
        post(env, "keynote", "check-out", {"registration_id": str(r.id)})
        env.db.expire_all()
        assert env.db.get(EventRegistration, r.id).checked_out_at is None


# ===========================================================================
# 16-19. Multiple sessions
# ===========================================================================


class TestMultiSession:
    def test_one_attendee_in_several_sessions_independently(self, env):
        r = reg(env)
        assert check_in(env, r, "keynote").json()["outcome"] == "checked_in"
        assert check_in(env, r, "workshop").json()["outcome"] == "checked_in"
        assert sorted(x.session_id for x in rows(env, registration_id=r.id)) == ["keynote", "workshop"]  # closing: not attended

    def test_sessions_do_not_affect_each_other(self, env):
        r = reg(env)
        for session in ("keynote", "workshop"):
            check_in(env, r, session)
        post(env, "keynote", "check-out", {"registration_id": str(r.id)})
        post(env, "workshop", "uncheck-in", {"registration_id": str(r.id)})
        by_session = {x.session_id: x for x in rows(env)}
        assert set(by_session) == {"keynote"} and by_session["keynote"].checked_out_at is not None

    def test_two_attendees_share_a_session(self, env):
        a, b = reg(env), reg(env)
        check_in(env, a)
        check_in(env, b)
        assert len(rows(env, session_id="keynote")) == 2

    def test_the_database_itself_refuses_a_duplicate(self, env):
        r = reg(env)
        add_row(env, r, "keynote")
        env.db.add(EventSessionAttendance(event_id=env.event.id, registration_id=r.id, session_id="keynote", checked_in_at=datetime.utcnow()))
        with pytest.raises(IntegrityError):
            env.db.commit()
        env.db.rollback()
        add_row(env, r, "workshop")  # a different session is fine
        assert len(rows(env)) == 2

    def test_a_scan_that_races_the_first_is_reported_as_already_checked_in(self, env, monkeypatch):
        r = reg(env)
        add_row(env, r, "keynote")  # the "other" scan, already committed
        real, calls = svc._row, {"n": 0}

        def stale_first_lookup(db, event_id, registration_id, session_id):
            calls["n"] += 1
            return None if calls["n"] == 1 else real(db, event_id, registration_id, session_id)

        monkeypatch.setattr(svc, "_row", stale_first_lookup)  # our own pre-check "missed" it
        resp = check_in(env, r)
        assert resp.status_code == 200 and resp.json()["outcome"] == "already_checked_in"
        assert len(rows(env)) == 1 and audits(env, "session_check_in") == []


# ===========================================================================
# 20-25. Security
# ===========================================================================

ACTIONS = [
    ("check-in", lambda r: {"registration_id": str(r.id)}, False),
    ("uncheck-in", lambda r: {"registration_id": str(r.id)}, True),
    ("check-out", lambda r: {"registration_id": str(r.id)}, True),
    ("batch-check-in", lambda r: {"participants": [{"registration_id": str(r.id)}]}, False),
]


class TestSecurity:
    def call(self, env, action, body_for, needs_row, user, event=None):
        event = event or env.event
        r = reg(env, event=event)
        if needs_row:
            add_row(env, r, "keynote")
        return r, client_for(env.db, user).post(url(env, "keynote", action, event), json=body_for(r))

    @pytest.mark.parametrize("action, body_for, needs_row", ACTIONS, ids=[a[0] for a in ACTIONS])
    @pytest.mark.parametrize("role", ["admin", "provider"])
    def test_owning_admin_and_provider_are_allowed(self, env, action, body_for, needs_row, role):
        _, resp = self.call(env, action, body_for, needs_row, staff_user(env.tenant, role))
        assert resp.status_code == 200, resp.text

    @pytest.mark.parametrize("action, body_for, needs_row", ACTIONS, ids=[a[0] for a in ACTIONS])
    def test_active_platform_super_admin_is_allowed_across_tenants(self, env, action, body_for, needs_row):
        _, resp = self.call(env, action, body_for, needs_row, super_admin_user())
        assert resp.status_code == 200, resp.text

    @pytest.mark.parametrize("action, body_for, needs_row", ACTIONS, ids=[a[0] for a in ACTIONS])
    @pytest.mark.parametrize("role", ["admin", "provider"])
    def test_foreign_tenant_staff_are_denied_and_change_nothing(self, env, action, body_for, needs_row, role):
        r = reg(env)
        if needs_row:
            add_row(env, r, "keynote")
        before = (len(rows(env)), len(audits(env)), snapshot(env.db))
        resp = client_for(env.db, staff_user(uuid4(), role)).post(url(env, "keynote", action), json=body_for(r))
        assert resp.status_code == 403
        assert (len(rows(env)), len(audits(env)), snapshot(env.db)) == before

    @pytest.mark.parametrize("action, body_for, needs_row", ACTIONS, ids=[a[0] for a in ACTIONS])
    def test_customers_are_denied_even_for_their_own_registration(self, env, action, body_for, needs_row):
        r = reg(env, "customer@example.com")
        if needs_row:
            add_row(env, r, "keynote")
        resp = client_for(env.db, customer_user("customer@example.com")).post(url(env, "keynote", action), json=body_for(r))
        assert resp.status_code == 403
        assert len(rows(env)) == (1 if needs_row else 0)

    @pytest.mark.parametrize("action, body_for, needs_row", ACTIONS, ids=[a[0] for a in ACTIONS])
    def test_inactive_super_admin_is_denied(self, env, action, body_for, needs_row):
        r = reg(env)
        if needs_row:
            add_row(env, r, "keynote")
        inactive = {**super_admin_user(), "status": "disabled"}
        assert client_for(env.db, inactive).post(url(env, "keynote", action), json=body_for(r)).status_code == 403

    @pytest.mark.parametrize("action, body_for, needs_row", ACTIONS, ids=[a[0] for a in ACTIONS])
    def test_anonymous_is_401(self, env, action, body_for, needs_row):
        r = reg(env)
        assert client_for(env.db, None).post(url(env, "keynote", action), json=body_for(r)).status_code == 401

    @pytest.mark.parametrize("action, body_for, needs_row", ACTIONS, ids=[a[0] for a in ACTIONS])
    def test_unknown_event_is_404_for_staff(self, env, action, body_for, needs_row):
        r = reg(env)
        resp = owner_client(env).post(f"{API}/{uuid4()}/sessions/keynote/{action}", json=body_for(r))
        assert resp.status_code == 404

    def test_staff_of_another_tenant_cannot_reach_a_registration_by_guessing_ids_on_their_own_event(self, env):
        """The attacker owns an event with the same session id; the victim's registration id must not resolve."""
        attacker_tenant = uuid4()
        attacker_event = make_event(env.db, attacker_tenant, make_enterprise(env.db, attacker_tenant),
                                    sessions=[{"id": "keynote", "title": "Mine"}])
        victim = reg(env)
        resp = client_for(env.db, staff_user(attacker_tenant, "admin")).post(
            url(env, "keynote", "check-in", attacker_event), json={"registration_id": str(victim.id)})
        assert resp.status_code == 404 and rows(env) == []

    def test_a_tenant_in_the_query_string_or_body_cannot_widen_access(self, env):
        r = reg(env)
        resp = client_for(env.db, staff_user(uuid4(), "admin")).post(
            url(env, "keynote", "check-in") + f"?tenant_id={env.tenant}", json={"registration_id": str(r.id), "tenant_id": str(env.tenant)})
        assert resp.status_code == 403 and rows(env) == []

    def test_a_deleted_event_is_not_manageable(self, env):
        r = reg(env)
        env.event.is_deleted = True
        env.db.commit()
        assert check_in(env, r).status_code == 404


# ===========================================================================
# 26-30. QR
# ===========================================================================


class TestQr:
    def test_valid_qr_and_session(self, env):
        r = reg(env)
        resp = post(env, "workshop", "check-in", {"qr_code": r.qr_code})
        assert resp.status_code == 200 and resp.json()["session_id"] == "workshop"

    def test_invalid_qr(self, env):
        assert post(env, "keynote", "check-in", {"qr_code": "0000NOPE"}).status_code == 404
        assert post(env, "keynote", "check-in", {"qr_code": r"'; DROP TABLE event_session_attendance;--"}).status_code == 404
        assert rows(env) == []

    def test_cancelled_and_refunded_attendee_qr(self, env):
        cancelled = reg(env, status="cancelled")
        refunded = reg(env, "refunded@example.com")
        make_order(env.db, env.event, "refunded@example.com", status="refunded", payment_status="refunded", created_at=LONG_AGO)
        assert post(env, "keynote", "check-in", {"qr_code": cancelled.qr_code}).status_code == 400
        assert post(env, "keynote", "check-in", {"qr_code": refunded.qr_code}).status_code == 400
        assert rows(env) == []

    def test_cross_event_qr_and_session_combinations(self, env):
        mine, theirs = reg(env), reg(env, event=env.other)
        # another event's QR on this event's session route
        assert post(env, "keynote", "check-in", {"qr_code": theirs.qr_code}).status_code == 404
        # this event's QR against a session that belongs to the other event
        assert post(env, "other-session", "check-in", {"qr_code": mine.qr_code}).status_code == 404
        # this event's QR on the other event's route + the other event's session
        assert post(env, "other-session", "check-in", {"qr_code": mine.qr_code}, event=env.other).status_code == 404
        # this event's QR on the other event's route + this event's session id
        assert post(env, "keynote", "check-in", {"qr_code": mine.qr_code}, event=env.other).status_code == 404
        assert rows(env) == []

    def test_repeated_qr_scan(self, env):
        r = reg(env)
        outcomes = [post(env, "keynote", "check-in", {"qr_code": r.qr_code}).json()["outcome"] for _ in range(3)]
        assert outcomes == ["checked_in", "already_checked_in", "already_checked_in"]
        assert len(rows(env)) == 1 and len(audits(env, "session_check_in")) == 1

    def test_the_existing_validate_qr_endpoint_and_registration_qr_are_unchanged(self, env):
        r = reg(env)
        resp = owner_client(env).post(f"{API}/{env.event.id}/validate-qr", json={"qr_code": r.qr_code})
        assert resp.status_code == 200
        assert set(resp.json()) == {"valid", "registration_id", "participant_name", "participant_email", "status",
                                    "event_id", "event_title", "ticket_type_id", "message"}
        assert resp.json()["valid"] is True


# ===========================================================================
# 31-34. Batch
# ===========================================================================


class TestBatch:
    def batch(self, env, participants, session="keynote", client=None, event=None):
        return (client or owner_client(env)).post(url(env, session, "batch-check-in", event), json={"participants": participants})

    def test_valid_batch(self, env):
        people = [reg(env) for _ in range(5)]
        resp = self.batch(env, [{"registration_id": str(p.id)} for p in people])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["total"], body["succeeded"], body["failed"]) == (5, 5, 0)
        assert body["session_id"] == "keynote" and body["event_id"] == str(env.event.id)
        assert {r["status"] for r in body["results"]} == {"checked_in"}
        assert len(rows(env)) == 5 and all(str(x.checked_in_by) == env.owner["id"] for x in rows(env))
        assert len(audits(env, "session_check_in")) == 5

    def test_mixed_batch_reports_each_entry_on_its_own(self, env):
        good = reg(env)
        already = reg(env)
        add_row(env, already, "keynote")
        cancelled = reg(env, status="cancelled")
        refunded = reg(env, "gone@example.com")
        make_order(env.db, env.event, "gone@example.com", status="refunded", payment_status="refunded", created_at=LONG_AGO)
        foreign = reg(env, event=env.other)
        via_qr = reg(env)
        resp = self.batch(env, [
            {"registration_id": str(good.id)},
            {"registration_id": str(already.id)},
            {"registration_id": str(cancelled.id)},
            {"registration_id": str(refunded.id)},
            {"registration_id": str(foreign.id)},
            {"registration_id": str(uuid4())},
            {"qr_code": "NOSUCHQR"},
            {"qr_code": via_qr.qr_code},
            {},
        ])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        statuses = [r["status"] for r in body["results"]]
        assert statuses == ["checked_in", "already_checked_in", "failed", "failed", "failed", "failed", "failed", "checked_in", "failed"]
        assert (body["total"], body["succeeded"], body["failed"]) == (9, 3, 6)
        messages = [r["message"] for r in body["results"]]
        assert "cancelled" in messages[2] and "refunded" in messages[3] and "not found" in messages[4]
        assert messages[8] == "registration_id or qr_code is required"
        # the valid ones are committed regardless of the failures around them
        assert sorted(str(x.registration_id) for x in rows(env)) == sorted(str(x.id) for x in (good, already, via_qr))

    def test_one_failure_does_not_invalidate_the_valid_attendees(self, env):
        people = [reg(env) for _ in range(4)]
        entries = [{"registration_id": str(people[0].id)}, {"registration_id": str(uuid4())},
                   {"registration_id": str(people[1].id)}, {"qr_code": "BAD"}, {"registration_id": str(people[2].id)}]
        body = self.batch(env, entries).json()
        assert [r["status"] for r in body["results"]].count("checked_in") == 3
        assert len(rows(env)) == 3 and len(audits(env, "session_check_in")) == 3

    def test_cross_event_entries_are_rejected(self, env):
        foreign = [reg(env, event=env.other) for _ in range(3)]
        body = self.batch(env, [{"registration_id": str(p.id)} for p in foreign] + [{"qr_code": p.qr_code} for p in foreign]).json()
        assert body["succeeded"] == 0 and body["failed"] == 6
        assert rows(env) == []

    def test_a_registration_listed_twice_is_checked_in_once(self, env):
        r = reg(env)
        body = self.batch(env, [{"registration_id": str(r.id)}, {"qr_code": r.qr_code}, {"registration_id": str(r.id)}]).json()
        assert [x["status"] for x in body["results"]] == ["checked_in", "already_checked_in", "already_checked_in"]
        assert len(rows(env)) == 1 and len(audits(env, "session_check_in")) == 1

    def test_whole_request_errors(self, env):
        r = reg(env)
        assert self.batch(env, [{"registration_id": str(r.id)}], session="nope").status_code == 404
        assert self.batch(env, [], ).status_code == 422
        assert self.batch(env, [{"qr_code": "x"}] * (svc_max() + 1)).status_code == 422
        cancelled_event = make_event(env.db, env.tenant, env.ent, status="cancelled", sessions=sessions_fixture())
        assert self.batch(env, [{"registration_id": str(r.id)}], event=cancelled_event).status_code == 400
        assert rows(env) == []

    def test_batch_records_the_audit_method_and_a_single_commit(self, env):
        people = [reg(env) for _ in range(3)]
        commits = count_commits(env.db)
        self.batch(env, [{"registration_id": str(p.id)} for p in people])
        assert len(commits) == 1
        assert {a.after["method"] for a in audits(env, "session_check_in")} == {"batch"}

    def test_statement_count_does_not_grow_with_the_batch(self, env):
        def run(n):
            entries = [{"registration_id": str(reg(env).id)} for _ in range(n)]  # built outside the counted block
            with Counter(env.db) as counter:
                assert self.batch(env, entries).status_code == 200
            return counter.selects

        assert run(3) == run(40)

    def test_qr_batch_statement_count_is_constant_too(self, env):
        def run(n):
            entries = [{"qr_code": reg(env).qr_code} for _ in range(n)]
            with Counter(env.db) as counter:
                assert self.batch(env, entries).status_code == 200
            return counter.selects

        assert run(3) == run(40)

    def test_a_race_during_the_batch_falls_back_to_insert_by_insert(self, env, monkeypatch):
        people = [reg(env) for _ in range(4)]
        flushes = {"n": 0}
        real_flush = env.db.flush

        def flaky_flush(*args, **kwargs):
            flushes["n"] += 1
            if flushes["n"] == 1:  # the fast path's combined flush loses a race
                raise IntegrityError("INSERT", {}, Exception("duplicate key"))
            return real_flush(*args, **kwargs)

        env.db.flush = flaky_flush
        try:
            body = self.batch(env, [{"registration_id": str(p.id)} for p in people]).json()
        finally:
            del env.db.flush
        assert [r["status"] for r in body["results"]] == ["checked_in"] * 4
        assert len(rows(env)) == 4 and len(audits(env, "session_check_in")) == 4  # nothing doubled by the retry

    def test_a_duplicate_found_only_during_the_insert_by_insert_pass_is_reported_alone(self, env, monkeypatch):
        people = [reg(env) for _ in range(3)]
        state = {"flushes": 0, "armed": False, "raced": False}
        real_flush, real_audit = env.db.flush, svc._log_audit

        def flaky_flush(*args, **kwargs):
            state["flushes"] += 1
            if state["flushes"] == 1:
                state["armed"] = True
                raise IntegrityError("INSERT", {}, Exception("duplicate key"))
            return real_flush(*args, **kwargs)

        def racing_audit(db, event_id, action, before, after, **kwargs):
            if state["armed"] and not state["raced"]:  # after the first attendee is in, someone else checks in the second
                state["raced"] = True
                db.execute(sa.insert(EventSessionAttendance).values(
                    id=uuid4(), event_id=event_id, registration_id=people[1].id, session_id="keynote",
                    checked_in_at=datetime.utcnow(), created_at=datetime.utcnow(), updated_at=datetime.utcnow()))
            return real_audit(db, event_id, action, before, after, **kwargs)

        env.db.flush = flaky_flush
        monkeypatch.setattr(svc, "_log_audit", racing_audit)
        try:
            body = self.batch(env, [{"registration_id": str(p.id)} for p in people]).json()
        finally:
            del env.db.flush
        assert [r["status"] for r in body["results"]] == ["checked_in", "already_checked_in", "checked_in"]
        assert len(rows(env)) == 3 and len(audits(env, "session_check_in")) == 2

    def test_bulk_refund_detection_agrees_with_the_single_attendee_check(self, env):
        """The batch decides 'refunded' from one query; it must match event_service._registration_is_refunded."""
        from app.services.event_service import _registration_is_refunded

        t = datetime(2026, 1, 1, 12, 0)
        scenarios = {
            "refunded-before": [(t - timedelta(hours=1), "refunded", "refunded")],
            "refunded-within-grace": [(t + timedelta(minutes=4), "refunded", "refunded")],
            "refunded-after-grace": [(t + timedelta(minutes=6), "refunded", "refunded")],
            "refunded-then-repaid": [(t - timedelta(hours=2), "refunded", "refunded"), (t - timedelta(minutes=1), "confirmed", "confirmed")],
            "only-requested": [(t - timedelta(hours=1), "refund_requested", "refund_requested")],
            "payment-refunded-only": [(t - timedelta(hours=1), "confirmed", "refunded")],
            "no-order": [],
        }
        regs = {}
        for name, orders in scenarios.items():
            email = f"{name}@example.com"
            regs[name] = reg(env, email, created_at=t)
            for created_at, order_status, payment_status in orders:
                make_order(env.db, env.event, email, status=order_status, payment_status=payment_status, created_at=created_at)
        upper = reg(env, "upper@example.com", created_at=t)
        make_order(env.db, env.event, "upper@example.com", status="refunded", payment_status="refunded", created_at=t - timedelta(hours=1))
        env.db.query(EventOrder).filter_by(participant_email="upper@example.com").update({"participant_email": "UPPER@Example.com"})
        other_event_reg = reg(env, "elsewhere@example.com", created_at=t)
        make_order(env.db, env.other, "elsewhere@example.com", status="refunded", payment_status="refunded", created_at=t - timedelta(hours=1))
        env.db.commit()
        everyone = [*regs.values(), upper, other_event_reg]
        bulk = svc._refunded_registration_ids(env.db, env.event.id, everyone)
        for registration in everyone:
            assert (registration.id in bulk) == _registration_is_refunded(env.db, registration), registration.participant_email
        assert bulk == {regs["refunded-before"].id, regs["refunded-within-grace"].id, regs["payment-refunded-only"].id, upper.id}


def svc_max():
    from app.schemas.event_session_attendance_schema import MAX_SESSION_BATCH

    return MAX_SESSION_BATCH


# ===========================================================================
# 42-44. Audit + transactions
# ===========================================================================


class TestAudit:
    def test_check_in_audit(self, env):
        r = reg(env)
        post(env, "keynote", "check-in", {"registration_id": str(r.id), "method": "qr_code"})
        (audit,) = audits(env, "session_check_in")
        assert audit.event_id == env.event.id and audit.changed_by == env.owner["id"] and audit.before is None
        assert audit.after["registration_id"] == str(r.id) and audit.after["session_id"] == "keynote"
        assert audit.after["session_title"] == "Opening Keynote" and audit.after["checked_in"] is True
        assert audit.after["method"] == "qr_code" and audit.after["checked_in_at"] and audit.after["checked_in_by"] == env.owner["id"]
        assert audit.after["participant_email"] == r.participant_email and audit.created_at is not None

    def test_uncheck_in_audit_keeps_the_history(self, env):
        r = reg(env)
        check_in(env, r)
        post(env, "keynote", "uncheck-in", {"registration_id": str(r.id), "reason": "scanned the wrong badge"})
        (audit,) = audits(env, "session_uncheck_in")
        assert audit.changed_by == env.owner["id"] and audit.notes == "scanned the wrong badge"
        assert audit.before["checked_in"] is True and audit.before["checked_in_at"] and audit.before["session_id"] == "keynote"
        assert audit.after["checked_in"] is False and audit.after["registration_id"] == str(r.id)
        # the check-in's own audit row is still there: the history survives the deleted attendance row
        assert len(audits(env, "session_check_in")) == 1

    def test_check_out_audit(self, env):
        r = reg(env)
        check_in(env, r)
        post(env, "keynote", "check-out", {"registration_id": str(r.id)})
        (audit,) = audits(env, "session_check_out")
        assert audit.before["checked_out_at"] is None and audit.after["checked_out_at"] and audit.changed_by == env.owner["id"]

    def test_rejected_operations_write_no_audit(self, env):
        cancelled = reg(env, status="cancelled")
        check_in(env, cancelled)
        check_in(env, reg(env), session="nope")
        post(env, "keynote", "uncheck-in", {"registration_id": str(reg(env).id)})
        post(env, "keynote", "check-out", {"registration_id": str(reg(env).id)})
        assert audits(env) == [] and rows(env) == []

    def test_each_operation_commits_exactly_once(self, env):
        r = reg(env)
        commits = count_commits(env.db)
        check_in(env, r)
        assert len(commits) == 1
        post(env, "keynote", "check-out", {"registration_id": str(r.id)})
        assert len(commits) == 2
        post(env, "keynote", "uncheck-in", {"registration_id": str(r.id)})
        assert len(commits) == 3
        check_in(env, r)
        check_in(env, r)  # a repeat commits nothing
        assert len(commits) == 4

    @pytest.mark.parametrize("action", ["check-in", "uncheck-in", "check-out", "batch-check-in"])
    def test_a_failed_commit_leaves_neither_the_change_nor_an_orphan_audit_row(self, env, action):
        r = reg(env)
        if action != "check-in" and action != "batch-check-in":
            add_row(env, r, "keynote")
        body = {"participants": [{"registration_id": str(r.id)}]} if action == "batch-check-in" else {"registration_id": str(r.id)}
        before = ([(x.registration_id, x.session_id, x.checked_out_at) for x in rows(env)], len(audits(env)))

        def boom():
            env.db.flush()  # the change and its audit row are really in the transaction when the commit fails
            raise RuntimeError("database went away")

        env.db.commit = boom
        try:
            resp = owner_client(env).post(url(env, "keynote", action), json=body)
        finally:
            del env.db.commit
        assert resp.status_code == 500
        assert ([(x.registration_id, x.session_id, x.checked_out_at) for x in rows(env)], len(audits(env))) == before
        assert audits(env, f"session_{action.replace('-', '_')}") == [] or action == "batch-check-in"
        assert audits(env, "session_check_in") == []  # (the pre-existing row was inserted directly, not through the API)

    def test_a_failed_batch_commit_leaves_no_partial_rows(self, env):
        people = [reg(env) for _ in range(3)]

        def boom():
            env.db.flush()  # the change and its audit row are really in the transaction when the commit fails
            raise RuntimeError("database went away")

        env.db.commit = boom
        try:
            resp = owner_client(env).post(url(env, "keynote", "batch-check-in"), json={"participants": [{"registration_id": str(p.id)} for p in people]})
        finally:
            del env.db.commit
        assert resp.status_code == 500 and rows(env) == [] and audits(env) == []


# ===========================================================================
# Session attendance changes nothing else
# ===========================================================================


class TestNothingElseChanges:
    def test_capacity_payment_registration_orders_waitlist_and_sessions_are_untouched(self, env):
        ticket = {"id": "general", "name": "General", "price": "500", "currency": "INR", "capacity": None}
        paid = make_event(env.db, env.tenant, env.ent, pricing_type="paid", price="500", ticket_types=[ticket], capacity="3",
                          sessions=sessions_fixture())
        buyer = reg(env, "buyer@example.com", event=paid)
        make_order(env.db, paid, "buyer@example.com", quantity="2", amount="1000", created_at=LONG_AGO)
        make_waitlist(env.db, paid, "queued@example.com", status="payment_pending", expires_at=datetime.utcnow() + timedelta(minutes=10))
        make_waitlist(env.db, paid, "waiting@example.com", status="waiting")
        before = snapshot(env.db)
        client = owner_client(env)
        base = f"{API}/{paid.id}/sessions"
        body = {"registration_id": str(buyer.id)}
        for action in ("check-in", "check-out", "uncheck-in", "check-in"):
            assert client.post(f"{base}/keynote/{action}", json=body).status_code == 200
        assert client.post(f"{base}/workshop/batch-check-in", json={"participants": [body]}).status_code == 200
        assert snapshot(env.db) == before
        assert env.db.query(EventWaitlist).filter_by(status="waiting").count() == 1  # nobody was promoted

    def test_event_level_check_in_behaves_exactly_as_before(self, env):
        r = reg(env)
        client = owner_client(env)
        resp = client.post(f"{API}/{env.event.id}/check-in", json={"registration_id": str(r.id), "session_id": "keynote", "method": "qr_code"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "attended" and resp.json()["session_id"] == "keynote"
        assert set(resp.json()) == {"message", "registration_id", "participant_name", "participant_email", "status", "checked_in_at", "session_id"}
        env.db.expire_all()
        fresh = env.db.get(EventRegistration, r.id)
        assert fresh.status == "attended" and fresh.session_id == "keynote" and fresh.checked_in_by is not None  # legacy behaviour intact
        assert rows(env) == []  # ... and it does NOT create session attendance

    def test_session_check_in_never_sets_event_level_state(self, env):
        r = reg(env)
        check_in(env, r)
        env.db.expire_all()
        fresh = env.db.get(EventRegistration, r.id)
        assert (fresh.status, fresh.checked_in_at, fresh.checked_in_by, fresh.checked_out_at, fresh.session_id) == ("confirmed", None, None, None, None)

    def test_event_level_uncheck_in_and_check_out_do_not_touch_session_attendance(self, env):
        r = reg(env)
        client = owner_client(env)
        client.post(f"{API}/{env.event.id}/check-in", json={"registration_id": str(r.id)})
        check_in(env, r)
        assert client.post(f"{API}/{env.event.id}/check-out", json={"registration_id": str(r.id)}).status_code == 200
        assert client.post(f"{API}/{env.event.id}/uncheck-in", json={"registration_id": str(r.id)}).status_code == 200
        assert [x.session_id for x in rows(env)] == ["keynote"] and rows(env)[0].checked_out_at is None

    def test_event_level_batch_check_in_is_unchanged(self, env):
        a, b = reg(env), reg(env)
        resp = owner_client(env).post(f"{API}/{env.event.id}/batch-check-in", json={"participants": [
            {"registration_id": str(a.id), "session_id": "keynote"}, {"registration_id": str(b.id)}]})
        assert resp.status_code == 200
        assert [r["status"] for r in resp.json()["results"]] == ["attended", "attended"]
        assert rows(env) == []


# ===========================================================================
# OpenAPI
# ===========================================================================


class TestOpenApi:
    @pytest.fixture(scope="class")
    def spec(self):
        from app.main import app as fastapi_app

        return fastapi_app.openapi()

    BASE = "/api/v1/events/{event_id}/sessions/{session_id}"

    def test_the_four_operations_are_documented_and_typed(self, spec):
        expected = {
            "check-in": ("EventSessionCheckInRequest", "EventSessionAttendanceResponse"),
            "uncheck-in": ("EventSessionUncheckInRequest", "EventSessionAttendanceResponse"),
            "check-out": ("EventSessionCheckOutRequest", "EventSessionAttendanceResponse"),
            "batch-check-in": ("EventSessionBatchCheckInRequest", "EventSessionBatchCheckInResponse"),
        }
        for action, (request_schema, response_schema) in expected.items():
            operation = spec["paths"][f"{self.BASE}/{action}"]["post"]
            assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(f"/{request_schema}")
            assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(f"/{response_schema}")
            assert {"400", "403", "404"} <= set(operation["responses"])
            assert operation["summary"] and operation["description"]

    def test_session_id_path_parameter_is_bounded_and_described(self, spec):
        params = {p["name"]: p for p in spec["paths"][f"{self.BASE}/check-in"]["post"]["parameters"]}
        assert params["session_id"]["schema"]["maxLength"] == 100 and params["session_id"]["description"]

    def test_security_matches_the_existing_event_manager_routes(self, spec):
        paths = spec["paths"]
        legacy = paths["/api/v1/events/{event_id}/check-in"]["post"].get("security")
        assert legacy
        for action in ("check-in", "uncheck-in", "check-out", "batch-check-in"):
            assert paths[f"{self.BASE}/{action}"]["post"].get("security") == legacy

    def test_event_level_routes_keep_their_contract(self, spec):
        paths = spec["paths"]
        body = paths["/api/v1/events/{event_id}/check-in"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        assert body.endswith("/EventCheckInRequest")
        assert "session_id" in spec["components"]["schemas"]["EventCheckInRequest"]["properties"]  # legacy field still there
        batch = paths["/api/v1/events/{event_id}/batch-check-in"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        assert batch.endswith("/EventBatchCheckInResponse")

    def test_batch_size_limit_is_documented(self, spec):
        participants = spec["components"]["schemas"]["EventSessionBatchCheckInRequest"]["properties"]["participants"]
        assert participants["minItems"] == 1 and participants["maxItems"] == svc_max()
