"""
Event Management Phase 2.5 — walk-in registration.

Real (in-memory SQLite) database via tests/event_sql_support.py: the duplicate/capacity checks, the one-transaction
register + check-in + session attendance, the audit rows and the attendee/dashboard integration run as SQL.

Covers: a walk-in is a normal registration (ticket/QR, attendee list, dashboard), organizer-only security and
mass-assignment protection, event eligibility (status, lifecycle, deleted) with the public registration window
bypassed, capacity (Phase 2.1 accounting + live waitlist offers, never the waitlist), duplicates, ticket types
and pricing, free vs paid (a payment is never faked; a pending payment is never admitted), immediate check-in via
the existing service, optional single-session attendance via the Phase 2.4 service, registration-form answers,
audit + atomicity, the `source` filter / CSV column / dashboard split, and OpenAPI.

Run:
    pytest tests/test_event_phase_2_5_walk_in.py -v
"""
import csv
import io
import re
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from event_sql_support import (
    API,
    EventAudit,
    EventOrder,
    EventRegistration,
    EventSessionAttendance,
    EventWaitlist,
    client_for,
    customer_user,
    make_enterprise,
    make_event,
    make_registration,
    make_session,
    make_waitlist,
    paid_ticket,
    reset_overrides,
    silence_side_effects,
    staff_user,
    super_admin_user,
)
from app.models.event_form_config_model import EventFormConfiguration, EventFormConfigurationVersion
import app.services.event_walk_in_service as walk_in_service

QR_FORMAT = re.compile(r"^[0-9A-F]{8}-[0-9A-F]{3}$")  # str(uuid4())[:12].upper(), what online registration/checkout mint


def sessions_fixture():
    return [
        {"id": "keynote", "title": "Opening Keynote"},
        {"id": "workshop", "title": "AI Workshop"},
        {"id": "closing", "title": "Closing Session"},
    ]


@pytest.fixture
def env(monkeypatch):
    apply_async = silence_side_effects(monkeypatch)
    db = make_session()
    tenant = uuid4()
    ent = make_enterprise(db, tenant)
    event = make_event(db, tenant, ent, capacity="50", sessions=sessions_fixture())
    other = make_event(db, tenant, ent, sessions=[{"id": "other-session", "title": "Elsewhere"}])
    owner = staff_user(tenant, "admin")
    yield SimpleNamespace(db=db, tenant=tenant, ent=ent, event=event, other=other, owner=owner, apply_async=apply_async)
    reset_overrides()
    db.close()


# ---------------------------------------------------------------------------- helpers


def owner_client(env):
    return client_for(env.db, env.owner)


def body(email=None, name="Walk In", **extra):
    return {"participant_name": name, "participant_email": email if email is not None else f"{uuid4().hex[:8]}@example.com", **extra}


def walk_in(env, payload, event=None, client=None):
    return (client or owner_client(env)).post(f"{API}/{(event or env.event).id}/walk-in", json=payload)


def regs(env, event=None):
    env.db.expire_all()
    return env.db.query(EventRegistration).filter_by(event_id=(event or env.event).id).all()


def orders(env, event=None):
    env.db.expire_all()
    return env.db.query(EventOrder).filter_by(event_id=(event or env.event).id).all()


def audits(env, action=None):
    env.db.expire_all()
    query = env.db.query(EventAudit)
    if action:
        query = query.filter(EventAudit.action == action)
    return query.order_by(EventAudit.created_at).all()


def session_rows(env):
    env.db.expire_all()
    return env.db.query(EventSessionAttendance).all()


def paid_event(env, capacity="10", tickets=None, **overrides):
    tickets = tickets or [paid_ticket("500", ticket_id="general")]
    return make_event(env.db, env.tenant, env.ent, pricing_type="paid", price="500", ticket_types=tickets, capacity=capacity,
                      sessions=sessions_fixture(), **overrides)


def checkout(env, event, email, quantity=1, ticket="general"):
    return client_for(env.db, customer_user(email)).post(f"{API}/{event.id}/checkout", json={
        "participant_name": email.split("@")[0], "participant_email": email, "ticket_type_id": ticket, "quantity": quantity})


def dashboard(env, event=None):
    resp = owner_client(env).get(f"{API}/{(event or env.event).id}/dashboard")
    assert resp.status_code == 200, resp.text
    return resp.json()


def count_commits(db):
    calls = []
    original = db.commit
    db.commit = lambda: (calls.append(1), original())[1]
    return calls


def snapshot(env):
    """Rows a rejected walk-in must leave exactly as they were."""
    env.db.expire_all()
    return (
        sorted(str(r.id) for r in env.db.query(EventRegistration)),
        sorted(str(o.id) for o in env.db.query(EventOrder)),
        sorted((str(w.id), w.status) for w in env.db.query(EventWaitlist)),
        env.db.query(EventAudit).count(),
        env.db.query(EventSessionAttendance).count(),
    )


def add_form(env, event, fields):
    config = EventFormConfiguration(name="Registration form", scope="global", status="published", is_active=True)
    env.db.add(config)
    env.db.commit()
    version = EventFormConfigurationVersion(
        configuration_id=config.id, version=1, status="published",
        sections=[{"id": "s1", "label": "Registration", "position": 1, "is_enabled": True, "fields": fields}])
    env.db.add(version)
    env.db.commit()
    event.form_configuration_version_id = version.id
    env.db.commit()


# ===========================================================================
# 1-5. A walk-in is a normal registration
# ===========================================================================


class TestBasicWalkIn:
    def test_free_event_walk_in_is_registered_and_checked_in(self, env):
        resp = walk_in(env, body("Asha@Example.com", name="  Asha Rao "))
        assert resp.status_code == 201, resp.text
        data = resp.json()
        registration = data["registration"]
        assert registration["participant_name"] == "Asha Rao" and registration["participant_email"] == "asha@example.com"
        assert registration["registration_status"] == "attended" and registration["is_checked_in"] is True
        assert registration["registration_source"] == "walk_in" and registration["payment_status"] == "free"
        assert data["payment"] == {"required": False, "status": "free", "amount": None, "currency": None, "order_id": None, "note": None}
        assert data["check_in"]["requested"] and data["check_in"]["performed"] and data["check_in"]["reason"] is None
        (row,) = regs(env)
        assert row.registration_source == "walk_in" and row.status == "attended" and orders(env) == []

    def test_a_normal_ticket_and_qr_are_generated(self, env):
        data = walk_in(env, body(check_in=False)).json()
        (row,) = regs(env)
        assert data["ticket"]["registration_reference"] == data["ticket"]["qr_code"] == row.qr_code == data["registration"]["registration_reference"]
        assert QR_FORMAT.match(row.qr_code)
        # the same format an online registration gets
        online = client_for(env.db, customer_user("online@example.com")).post(
            f"{API}/{env.event.id}/registrations", json={"participant_name": "Online", "participant_email": "online@example.com"}).json()
        assert QR_FORMAT.match(online["qr_code"]) and online["qr_code"] != row.qr_code

    def test_the_qr_image_endpoint_serves_it(self, env):
        pytest.importorskip("qrcode")
        data = walk_in(env, body(check_in=False)).json()
        path = data["ticket"]["qr_image_path"]
        assert path == f"{API}/{env.event.id}/registrations/{data['registration']['registration_id']}/qr"
        resp = owner_client(env).get(path)
        assert resp.status_code == 200 and resp.headers["content-type"] == "image/png"

    def test_the_existing_qr_flows_accept_it(self, env):
        reference = walk_in(env, body(check_in=False)).json()["ticket"]["qr_code"]
        client = owner_client(env)
        valid = client.post(f"{API}/{env.event.id}/validate-qr", json={"qr_code": reference}).json()
        assert valid["valid"] is True and valid["status"] == "confirmed"
        assert client.post(f"{API}/{env.event.id}/check-in", json={"qr_code": reference}).json()["status"] == "attended"
        assert client.post(f"{API}/{env.event.id}/sessions/keynote/check-in", json={"qr_code": reference}).status_code == 200

    def test_it_appears_in_the_attendee_list(self, env):
        data = walk_in(env, body("listed@example.com", name="Listed Person")).json()
        listing = owner_client(env).get(f"{API}/{env.event.id}/attendees", params={"q": data["ticket"]["qr_code"]}).json()
        (item,) = listing["items"]
        assert item["participant_email"] == "listed@example.com" and item["registration_source"] == "walk_in"
        detail = owner_client(env).get(f"{API}/{env.event.id}/registrations/{item['registration_id']}").json()
        assert detail == item == data["registration"]

    def test_it_is_counted_in_the_dashboard(self, env):
        client_for(env.db, customer_user("o@example.com")).post(
            f"{API}/{env.event.id}/registrations", json={"participant_name": "O", "participant_email": "o@example.com"})
        walk_in(env, body())
        walk_in(env, body(check_in=False))
        registrations = dashboard(env)["registrations"]
        assert (registrations["total"], registrations["active"], registrations["attended"], registrations["confirmed"]) == (3, 3, 1, 2)
        assert (registrations["online"], registrations["walk_in"]) == (1, 2)
        assert dashboard(env)["capacity"]["seats_taken"] == 3
        assert dashboard(env)["attendance"]["checked_in"] == 1

    def test_online_registration_and_checkout_are_untouched(self, env):
        online = client_for(env.db, customer_user("o@example.com")).post(
            f"{API}/{env.event.id}/registrations", json={"participant_name": "O", "participant_email": "o@example.com"})
        assert online.status_code == 201
        payload = online.json()
        assert payload["status"] == "confirmed" and payload["qr_code"] and payload["id"] and payload["registration_source"] is None
        paid = paid_event(env)
        assert checkout(env, paid, "buyer@example.com").status_code == 201
        env.db.expire_all()
        assert {r.registration_source for r in env.db.query(EventRegistration)} == {None}  # NULL = online, nothing written

    def test_the_legacy_registration_list_is_still_a_bare_array(self, env):
        walk_in(env, body("a@example.com", check_in=False))
        listing = owner_client(env).get(f"{API}/{env.event.id}/registrations").json()
        assert isinstance(listing, list) and listing[0]["registration_source"] == "walk_in" and listing[0]["status"] == "confirmed"


# ===========================================================================
# 6-12. Security
# ===========================================================================


class TestSecurity:
    def test_owning_admin_and_provider_are_allowed(self, env):
        for role in ("admin", "provider"):
            assert walk_in(env, body(), client=client_for(env.db, staff_user(env.tenant, role))).status_code == 201

    def test_active_platform_super_admin_is_allowed_across_tenants(self, env):
        assert walk_in(env, body(), client=client_for(env.db, super_admin_user())).status_code == 201

    def test_customers_cannot_walk_anyone_in_even_themselves(self, env):
        before = snapshot(env)
        resp = walk_in(env, body("me@example.com"), client=client_for(env.db, customer_user("me@example.com")))
        assert resp.status_code == 403
        assert snapshot(env) == before

    @pytest.mark.parametrize("role", ["admin", "provider"])
    def test_foreign_tenant_staff_are_denied_and_change_nothing(self, env, role):
        before = snapshot(env)
        resp = walk_in(env, body("secret@example.com"), client=client_for(env.db, staff_user(uuid4(), role)))
        assert resp.status_code == 403 and "secret@example.com" not in resp.text
        assert snapshot(env) == before

    def test_inactive_super_admin_is_denied(self, env):
        before = snapshot(env)
        assert walk_in(env, body(), client=client_for(env.db, {**super_admin_user(), "status": "disabled"})).status_code == 403
        assert snapshot(env) == before

    def test_no_token_is_401(self, env):
        assert walk_in(env, body(), client=client_for(env.db, None)).status_code == 401
        assert regs(env) == []

    def test_unknown_event_is_404_and_a_deleted_event_is_404(self, env):
        assert owner_client(env).post(f"{API}/{uuid4()}/walk-in", json=body()).status_code == 404
        env.event.is_deleted = True
        env.db.commit()
        assert walk_in(env, body()).status_code == 404

    @pytest.mark.parametrize("field, value", [
        ("tenant_id", str(uuid4())), ("enterprise_id", str(uuid4())), ("event_id", str(uuid4())),
        ("registration_source", "online"), ("status", "attended"), ("qr_code", "MYOWNQR"), ("payment_status", "confirmed"),
        ("amount", "0"), ("order_id", str(uuid4())), ("group_size", 3), ("group_members", [{"name": "x", "email": "x@example.com"}]),
        ("checked_in_by", str(uuid4())), ("payment_provider", "razorpay"), ("payment_id", "pay_FAKE123"),
    ])
    def test_ownership_and_payment_fields_cannot_be_supplied(self, env, field, value):
        before = snapshot(env)
        resp = walk_in(env, body(**{field: value}))
        assert resp.status_code == 422 and field in resp.text
        assert snapshot(env) == before

    def test_the_event_comes_from_the_path_not_the_body(self, env):
        walk_in(env, body("a@example.com", check_in=False))
        (row,) = regs(env)
        assert row.event_id == env.event.id and regs(env, env.other) == []

    def test_a_walk_in_never_lands_on_another_tenants_event(self, env):
        foreign_tenant = uuid4()
        foreign = make_event(env.db, foreign_tenant, make_enterprise(env.db, foreign_tenant))
        assert walk_in(env, body(), event=foreign).status_code == 403
        assert regs(env, foreign) == []


# ===========================================================================
# 13-17. Event eligibility
# ===========================================================================


class TestEligibility:
    def test_a_published_event_is_allowed(self, env):
        assert walk_in(env, body()).status_code == 201

    @pytest.mark.parametrize("event_status", ["cancelled", "completed", "archived", "suspended"])
    def test_closed_events_are_rejected(self, env, event_status):
        event = make_event(env.db, env.tenant, env.ent, status=event_status)
        resp = walk_in(env, body(), event=event)
        assert resp.status_code == 400 and event_status in resp.json()["detail"] and "closed" in resp.json()["detail"]
        assert regs(env, event) == []

    @pytest.mark.parametrize("event_status", ["draft", "pending_approval", "approved", "rejected"])
    def test_events_that_are_not_published_are_rejected(self, env, event_status):
        event = make_event(env.db, env.tenant, env.ent, status=event_status)
        resp = walk_in(env, body(), event=event)
        assert resp.status_code == 400 and "not open for registration" in resp.json()["detail"]

    def test_a_finished_event_is_rejected_by_lifecycle_even_though_its_status_is_still_published(self, env):
        now = datetime.utcnow()
        finished = make_event(env.db, env.tenant, env.ent, status="published", start_date=now - timedelta(days=3), end_date=now - timedelta(days=2))
        resp = walk_in(env, body(), event=finished)
        assert resp.status_code == 400 and "finished" in resp.json()["detail"]
        assert regs(env, finished) == []

    def test_an_ongoing_event_takes_walk_ins(self, env):
        now = datetime.utcnow()
        ongoing = make_event(env.db, env.tenant, env.ent, start_date=now - timedelta(hours=2), end_date=now + timedelta(hours=2))
        assert walk_in(env, body(), event=ongoing).status_code == 201

    def test_an_upcoming_event_and_one_without_dates_take_walk_ins(self, env):
        assert walk_in(env, body()).status_code == 201  # env.event starts in 10 days
        undated = make_event(env.db, env.tenant, env.ent, start_date=None, end_date=None)
        assert walk_in(env, body(), event=undated).status_code == 201

    @pytest.mark.parametrize("event_status", ["draft", "published", "cancelled", "completed", "archived", "suspended"])
    def test_status_rules_match_online_registration(self, env, event_status):
        event = make_event(env.db, env.tenant, env.ent, status=event_status)
        online = client_for(env.db, customer_user("c@example.com")).post(
            f"{API}/{event.id}/registrations", json={"participant_name": "C", "participant_email": "c@example.com"}).status_code
        assert (walk_in(env, body(), event=event).status_code == 201) == (online == 201)


# ===========================================================================
# 18-19. Registration window
# ===========================================================================


class TestRegistrationWindow:
    def closed_window_event(self, env, **overrides):
        now = datetime.utcnow()
        return make_event(env.db, env.tenant, env.ent, registration_open_at=now - timedelta(days=5),
                          registration_close_at=now - timedelta(days=1), capacity="1", **overrides)

    def test_walk_in_works_when_the_public_window_is_closed(self, env):
        event = self.closed_window_event(env)
        online = client_for(env.db, customer_user("c@example.com")).post(
            f"{API}/{event.id}/registrations", json={"participant_name": "C", "participant_email": "c@example.com"})
        assert online.status_code == 400 and "Registration closed" in online.json()["detail"]
        assert walk_in(env, body(), event=event).status_code == 201

    def test_walk_in_works_before_the_window_opens_and_after_the_cutoff(self, env):
        now = datetime.utcnow()
        not_open = make_event(env.db, env.tenant, env.ent, registration_open_at=now + timedelta(days=2))
        cut_off = make_event(env.db, env.tenant, env.ent, registration_cutoff=now - timedelta(hours=1))
        for event in (not_open, cut_off):
            assert client_for(env.db, customer_user("c@example.com")).post(
                f"{API}/{event.id}/registrations", json={"participant_name": "C", "participant_email": "c@example.com"}).status_code == 400
            assert walk_in(env, body(), event=event).status_code == 201

    def test_capacity_is_still_enforced_outside_the_window(self, env):
        event = self.closed_window_event(env)
        assert walk_in(env, body(), event=event).status_code == 201
        full = walk_in(env, body(), event=event)
        assert full.status_code == 400 and "full capacity" in full.json()["detail"]

    def test_status_lifecycle_and_ownership_are_still_enforced_outside_the_window(self, env):
        cancelled = self.closed_window_event(env, status="cancelled")
        assert walk_in(env, body(), event=cancelled).status_code == 400
        assert walk_in(env, body(), event=self.closed_window_event(env), client=client_for(env.db, staff_user(uuid4(), "admin"))).status_code == 403


# ===========================================================================
# 20-24. Capacity
# ===========================================================================


class TestCapacity:
    def small_event(self, env, capacity="2", **overrides):
        return make_event(env.db, env.tenant, env.ent, capacity=capacity, **overrides)

    def test_a_walk_in_that_fits_succeeds_up_to_the_last_seat(self, env):
        event = self.small_event(env, "2")
        make_registration(env.db, event, "online@example.com")
        assert walk_in(env, body(), event=event).status_code == 201  # seat 2 of 2

    def test_a_full_event_rejects_the_walk_in_and_creates_nothing(self, env):
        event = self.small_event(env, "2")
        for i in range(2):
            make_registration(env.db, event, f"r{i}@example.com")
        before = snapshot(env)
        resp = walk_in(env, body(), event=event)
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Event is at full capacity (2 participants). Walk-in registrations do not use the waitlist."
        assert snapshot(env) == before

    def test_cancelled_registrations_free_their_seat(self, env):
        event = self.small_event(env, "1")
        make_registration(env.db, event, "gone@example.com", status="cancelled")
        assert walk_in(env, body(), event=event).status_code == 201

    def test_multi_quantity_orders_hold_all_their_seats(self, env):
        event = paid_event(env, capacity="4")
        assert checkout(env, event, "family@example.com", quantity=3).status_code == 201  # 3 seats: 1 registration + 2 extra
        assert dashboard(env, event)["capacity"]["seats_taken"] == 3
        assert walk_in(env, body(ticket_type_id="general", check_in=False), event=event).status_code == 201  # seat 4 of 4
        full = walk_in(env, body(ticket_type_id="general", check_in=False), event=event)
        assert full.status_code == 400 and "full capacity" in full.json()["detail"]
        assert dashboard(env, event)["capacity"]["seats_taken"] == 4

    def test_a_live_paid_waitlist_offer_still_reserves_its_seat(self, env):
        event = self.small_event(env, "2")
        make_registration(env.db, event, "a@example.com")
        make_waitlist(env.db, event, "paying@example.com", status="payment_pending", expires_at=datetime.utcnow() + timedelta(minutes=10))
        assert dashboard(env, event)["capacity"]["available_seats"] == 0
        resp = walk_in(env, body(), event=event)
        assert resp.status_code == 400 and "full capacity" in resp.json()["detail"]
        assert len(regs(env, event)) == 1

    def test_an_offer_with_no_expiry_is_live_and_an_expired_one_is_not(self, env):
        event = self.small_event(env, "1")
        make_waitlist(env.db, event, "forever@example.com", status="payment_pending", expires_at=None)
        assert walk_in(env, body(), event=event).status_code == 400
        env.db.query(EventWaitlist).update({"payment_offer_expires_at": datetime.utcnow() - timedelta(minutes=1)})
        env.db.commit()
        assert walk_in(env, body(), event=event).status_code == 201  # the lapsed offer no longer holds the seat

    def test_waiting_entries_do_not_reserve_and_are_never_promoted(self, env):
        event = self.small_event(env, "2")
        make_waitlist(env.db, event, "queued@example.com", status="waiting")
        assert walk_in(env, body(), event=event).status_code == 201
        env.db.expire_all()
        assert [w.status for w in env.db.query(EventWaitlist)] == ["waiting"]
        env.apply_async.assert_not_called()

    def test_a_walk_in_never_creates_or_changes_waitlist_entries(self, env):
        event = self.small_event(env, "1")
        make_registration(env.db, event, "a@example.com")
        make_waitlist(env.db, event, "w1@example.com", status="waiting")
        make_waitlist(env.db, event, "w2@example.com", status="payment_pending", expires_at=datetime.utcnow() - timedelta(minutes=5))
        before = snapshot(env)
        assert walk_in(env, body(), event=event).status_code == 400  # full: rejected, not queued
        assert snapshot(env) == before
        env.apply_async.assert_not_called()

    def test_the_reserved_seat_count_is_the_dashboards_and_the_promotions(self, env):
        from app.services.event_service import _try_promote_from_waitlist

        event = paid_event(env, capacity="3")  # on a paid event promotion makes a payment offer (a free event registers directly)
        future, past = datetime.utcnow() + timedelta(minutes=10), datetime.utcnow() - timedelta(minutes=10)
        for email, status_, expires in (("live1@example.com", "payment_pending", future), ("live2@example.com", "payment_pending", None),
                                        ("late@example.com", "payment_pending", past), ("q@example.com", "waiting", None),
                                        ("p@example.com", "promoted", None), ("l@example.com", "left", None)):
            make_waitlist(env.db, event, email, status=status_, expires_at=expires)
        assert walk_in_service.live_offer_count(env.db, event.id) == dashboard(env, event)["capacity"]["seats_reserved"] == 2
        # and promotion agrees: 2 of 3 seats are held, so one more offer fits and a second does not
        _try_promote_from_waitlist(env.db, event.id, event)
        env.db.commit()
        assert walk_in_service.live_offer_count(env.db, event.id) == 3
        assert walk_in(env, body(ticket_type_id="general"), event=event).status_code == 400

    def test_dashboard_availability_agrees_with_what_a_walk_in_can_do(self, env):
        event = self.small_event(env, "3")
        for step in range(4):
            available = dashboard(env, event)["capacity"]["available_seats"]
            resp = walk_in(env, body(check_in=False), event=event)
            assert (resp.status_code == 201) == (available > 0), step

    def test_the_ticket_type_capacity_is_enforced(self, env):
        tickets = [paid_ticket("500", capacity="1", ticket_id="vip"), paid_ticket("100", ticket_id="general")]
        event = paid_event(env, capacity="10", tickets=tickets)
        assert checkout(env, event, "first@example.com", ticket="vip").status_code == 201
        full = walk_in(env, body(ticket_type_id="vip", check_in=False), event=event)
        assert full.status_code == 400 and "Ticket type at capacity (1)" in full.json()["detail"]
        assert walk_in(env, body(ticket_type_id="general", check_in=False), event=event).status_code == 201

    def test_max_participants_is_enforced_like_free_registration(self, env):
        event = make_event(env.db, env.tenant, env.ent, capacity="10", max_participants="1")
        assert walk_in(env, body(), event=event).status_code == 201
        resp = walk_in(env, body(), event=event)
        assert resp.status_code == 400 and "Maximum participants reached (1)" in resp.json()["detail"]

    @pytest.mark.parametrize("raw, allowed", [(None, True), ("", True), ("abc", True), ("inf", True), ("0", False), ("1", True)])
    def test_unusable_capacity_means_unlimited_and_zero_means_full(self, env, raw, allowed):
        event = make_event(env.db, env.tenant, env.ent, capacity=raw)
        assert (walk_in(env, body(), event=event).status_code == 201) == allowed

    def test_the_seats_are_counted_by_the_same_function_online_flows_use(self, env, monkeypatch):
        seen = []
        real = walk_in_service._seats_taken
        monkeypatch.setattr(walk_in_service, "_seats_taken", lambda *a, **k: (seen.append(a[1:]), real(*a, **k))[1])
        event = self.small_event(env, "5")
        walk_in(env, body(), event=event)
        assert seen == [(event.id,)]


# ===========================================================================
# 25-26. Duplicates
# ===========================================================================

DUPLICATE_MESSAGE = "Already registered for this event. Cancel your existing registration before registering again."


class TestDuplicates:
    def test_an_active_registration_blocks_a_second_one_with_the_online_message(self, env):
        make_registration(env.db, env.event, "dup@example.com")
        before = snapshot(env)
        resp = walk_in(env, body("dup@example.com"))
        assert resp.status_code == 409 and resp.json()["detail"] == DUPLICATE_MESSAGE
        online = client_for(env.db, customer_user("dup@example.com")).post(
            f"{API}/{env.event.id}/registrations", json={"participant_name": "D", "participant_email": "dup@example.com"})
        assert online.status_code == 409 and online.json()["detail"] == DUPLICATE_MESSAGE  # the same conflict, same words
        assert snapshot(env) == before

    @pytest.mark.parametrize("typed", ["DUP@EXAMPLE.COM", "Dup@Example.com", "  dup@example.com  "])
    def test_email_case_and_whitespace_do_not_get_around_it(self, env, typed):
        make_registration(env.db, env.event, "dup@example.com")
        assert walk_in(env, body(typed)).status_code == 409
        assert len(regs(env)) == 1

    def test_a_stored_mixed_case_email_still_blocks(self, env):
        """Older rows may hold the email with capitals; the comparison is on lower(email), like online registration."""
        make_registration(env.db, env.event, "mixed@example.com")
        env.db.query(EventRegistration).update({"participant_email": "Mixed@Example.com"})
        env.db.commit()
        assert walk_in(env, body("mixed@example.com")).status_code == 409
        assert len(regs(env)) == 1

    def test_an_attended_registration_also_blocks(self, env):
        make_registration(env.db, env.event, "in@example.com", status="attended")
        assert walk_in(env, body("in@example.com")).status_code == 409

    def test_a_cancelled_registration_does_not_block_re_registration(self, env):
        make_registration(env.db, env.event, "back@example.com", status="cancelled")
        assert walk_in(env, body("back@example.com")).status_code == 201
        assert sorted(r.status for r in regs(env)) == ["attended", "cancelled"]

    def test_two_walk_ins_for_the_same_person_create_one_registration(self, env):
        assert walk_in(env, body("same@example.com")).status_code == 201
        assert walk_in(env, body("SAME@example.com")).status_code == 409
        assert len(regs(env)) == 1

    def test_the_same_person_can_walk_in_to_a_different_event(self, env):
        assert walk_in(env, body("same@example.com")).status_code == 201
        assert walk_in(env, body("same@example.com"), event=env.other).status_code == 201

    def test_a_race_lost_to_the_unique_index_is_a_409_and_leaves_nothing(self, env):
        real_flush = env.db.flush

        def losing_flush(*args, **kwargs):
            raise IntegrityError("INSERT", {}, Exception("duplicate key value violates unique constraint uq_event_reg_active"))

        env.db.flush = losing_flush
        try:
            resp = walk_in(env, body())
        finally:
            del env.db.flush
        assert resp.status_code == 409 and resp.json()["detail"] == DUPLICATE_MESSAGE
        assert regs(env) == [] and audits(env) == []
        assert real_flush is not None

    @pytest.mark.parametrize("bad", ["", "no-at-sign", "two@@example.com", "spaces in@example.com", "@example.com", "a@b"])
    def test_a_malformed_email_is_rejected(self, env, bad):
        assert walk_in(env, body(bad)).status_code == 422
        assert regs(env) == []


# ===========================================================================
# 27-29. Ticket types + pricing
# ===========================================================================


class TestTickets:
    def test_a_valid_ticket_type_succeeds_and_is_stored(self, env):
        event = paid_event(env, tickets=[paid_ticket("500", ticket_id="general"), paid_ticket("900", ticket_id="vip")])
        data = walk_in(env, body(ticket_type_id="vip", check_in=False), event=event).json()
        assert data["registration"]["ticket_type_id"] == "vip" and data["registration"]["ticket_type_name"] == "General"
        assert data["payment"]["amount"] == 900.0 and data["ticket"]["ticket_type_id"] == "vip"
        (row,) = regs(env, event)
        assert row.ticket_type_id == "vip"

    def test_a_ticket_type_of_another_event_is_rejected(self, env):
        event = paid_event(env, tickets=[paid_ticket("500", ticket_id="general")])
        elsewhere = paid_event(env, tickets=[paid_ticket("500", ticket_id="foreign-ticket")])
        before = snapshot(env)
        resp = walk_in(env, body(ticket_type_id="foreign-ticket"), event=event)
        assert resp.status_code == 404 and resp.json()["detail"] == "Ticket type not found"
        assert snapshot(env) == before
        assert elsewhere.id != event.id

    @pytest.mark.parametrize("ticket_id", ["nope", "GENERAL", "", "0"])
    def test_an_invalid_ticket_type_is_rejected(self, env, ticket_id):
        event = paid_event(env)
        payload = body(ticket_type_id=ticket_id) if ticket_id else body(ticket_type_id=ticket_id)
        resp = walk_in(env, payload, event=event)
        assert resp.status_code in (400, 404) and regs(env, event) == []

    def test_a_free_event_validates_a_ticket_type_that_is_sent(self, env):
        event = make_event(env.db, env.tenant, env.ent, ticket_types=[{"id": "free-pass", "name": "Free pass", "price": "0"}])
        assert walk_in(env, body(ticket_type_id="free-pass"), event=event).status_code == 201
        assert walk_in(env, body(ticket_type_id="not-a-ticket"), event=event).status_code == 404
        assert walk_in(env, body(), event=event).status_code == 201  # optional when free

    def test_a_paid_event_with_ticket_types_requires_one(self, env):
        event = paid_event(env)
        resp = walk_in(env, body(), event=event)
        assert resp.status_code == 400 and "ticket_type_id is required" in resp.json()["detail"]

    def test_a_paid_event_without_ticket_types_uses_the_event_price(self, env):
        event = make_event(env.db, env.tenant, env.ent, pricing_type="paid", price="250", ticket_types=[])
        data = walk_in(env, body(check_in=False), event=event).json()
        assert data["payment"]["amount"] == 250.0 and data["payment"]["status"] == "pending"

    def test_the_price_comes_from_the_checkouts_own_rules(self, env):
        future = (datetime.utcnow() + timedelta(days=2)).isoformat()
        past = (datetime.utcnow() - timedelta(days=2)).isoformat()
        tickets = [
            {"id": "early", "name": "Early", "price": "500", "early_bird_price": "300", "early_bird_until": future},
            {"id": "expired", "name": "Expired early", "price": "500", "early_bird_price": "300", "early_bird_until": past},
            {"id": "promo", "name": "Promo", "price": "500", "promo_price": "450"},
        ]
        event = paid_event(env, capacity="20", tickets=tickets)
        amounts = {t: walk_in(env, body(ticket_type_id=t, check_in=False), event=event).json()["payment"]["amount"] for t in ("early", "expired", "promo")}
        assert amounts == {"early": 300.0, "expired": 500.0, "promo": 450.0}
        for ticket, walk_in_amount in (("early", 300.0), ("promo", 450.0)):  # ... exactly what an online buyer is charged
            online = checkout(env, event, f"{ticket}@example.com", ticket=ticket).json()
            assert float(online["amount"]) == walk_in_amount

    @pytest.mark.parametrize("price", ["abc", "-5", "", "NaN"])
    def test_an_unusable_price_is_rejected_not_guessed(self, env, price):
        event = paid_event(env, tickets=[{"id": "odd", "name": "Odd", "price": price}])
        if price == "":
            event.price = "abc"
            env.db.commit()
        resp = walk_in(env, body(ticket_type_id="odd"), event=event)
        assert resp.status_code == 400 and "price" in resp.json()["detail"]
        assert regs(env, event) == [] and orders(env, event) == []

    def test_the_ticket_currency_is_used(self, env):
        event = paid_event(env, tickets=[{"id": "usd", "name": "USD ticket", "price": "20", "currency": "USD"}])
        assert walk_in(env, body(ticket_type_id="usd", check_in=False), event=event).json()["payment"]["currency"] == "USD"


# ===========================================================================
# 30-33. Free / paid
# ===========================================================================


class TestPayment:
    def test_free_walk_in_has_no_order(self, env):
        walk_in(env, body())
        assert orders(env) == []

    def test_paid_walk_in_follows_the_pending_payment_policy(self, env):
        event = paid_event(env)
        resp = walk_in(env, body("buyer@example.com", ticket_type_id="general"), event=event)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        (order,) = orders(env, event)
        assert (order.status, order.payment_status, order.amount, order.quantity, order.currency) == ("confirmed", "pending", "500.0", "1", "INR")
        assert order.ticket_type_id == "general" and order.participant_email == "buyer@example.com"
        assert data["payment"] == {
            "required": True, "status": "pending", "amount": 500.0, "currency": "INR", "order_id": str(order.id),
            "note": walk_in_service._PENDING_NOTE}
        assert data["registration"]["payment_status"] == "pending" and data["registration"]["registration_status"] == "confirmed"
        assert data["registration"]["order_id"] == str(order.id) and data["registration"]["amount"] == 500.0

    def test_no_payment_success_is_faked_anywhere(self, env, monkeypatch):
        sent = []
        monkeypatch.setattr("app.services.notification_triggers.notify_payment_success", lambda *a, **k: sent.append("payment"))
        monkeypatch.setattr("app.services.notification_triggers.notify_registration_confirmation", lambda *a, **k: sent.append("confirmation"))
        event = paid_event(env)
        walk_in(env, body(ticket_type_id="general"), event=event)
        (order,) = orders(env, event)
        assert order.payment_status != "confirmed" and order.payment_provider is None  # no provider claimed, no gateway id anywhere
        assert not hasattr(order, "payment_id") and sent == []  # neither a payment nor a "you are confirmed" message
        body_ = dashboard(env, event)
        assert body_["revenue"]["total_revenue"] == 0.0 and body_["revenue"]["paid_orders"] == 0
        assert (body_["orders"]["successful"], body_["orders"]["pending"]) == (0, 1)

    def test_a_pending_walk_in_is_not_admitted_and_says_why(self, env):
        event = paid_event(env)
        data = walk_in(env, body(ticket_type_id="general"), event=event).json()
        assert data["check_in"] == {"requested": True, "performed": False, "checked_in_at": None, "reason": "payment_pending"}
        assert data["registration"]["is_checked_in"] is False and data["registration"]["checked_in_at"] is None
        (row,) = regs(env, event)
        assert row.status == "confirmed" and row.checked_in_at is None and row.checked_in_by is None
        assert audits(env, "check_in") == []
        assert "payment is pending" in data["message"]

    def test_a_pending_walk_in_is_not_admitted_to_a_session_either(self, env):
        event = paid_event(env)
        data = walk_in(env, body(ticket_type_id="general", session_id="keynote"), event=event).json()
        assert data["session_check_in"] == {"requested": True, "session_id": "keynote", "performed": False, "reason": "payment_pending"}
        assert session_rows(env) == [] and audits(env, "session_check_in") == []

    def test_a_ticket_that_costs_nothing_owes_nothing_and_is_admitted(self, env):
        event = paid_event(env, tickets=[{"id": "comp", "name": "Complimentary", "price": "0"}])
        data = walk_in(env, body(ticket_type_id="comp"), event=event).json()
        (order,) = orders(env, event)
        assert (order.status, order.payment_status, order.amount) == ("confirmed", "confirmed", "0.0")  # nothing to pay: not a fake success
        assert data["payment"]["required"] is False and data["payment"]["status"] == "paid"
        assert data["check_in"]["performed"] is True and data["registration"]["registration_status"] == "attended"

    def test_a_pending_walk_in_can_be_abandoned_through_the_existing_cancel_flow(self, env):
        event = paid_event(env, capacity="1")
        data = walk_in(env, body(ticket_type_id="general"), event=event).json()
        reg_id = data["registration"]["registration_id"]
        assert owner_client(env).delete(f"{API}/{event.id}/registrations/{reg_id}").status_code == 200
        assert walk_in(env, body(ticket_type_id="general"), event=event).status_code == 201  # the seat came back

    def test_a_pending_walk_in_can_be_checked_in_only_by_the_explicit_existing_endpoint(self, env):
        """The generic check-in never verified payment (unchanged); the payment stays pending and visible."""
        event = paid_event(env)
        data = walk_in(env, body(ticket_type_id="general"), event=event).json()
        assert owner_client(env).post(f"{API}/{event.id}/check-in", json={"qr_code": data["ticket"]["qr_code"]}).status_code == 200
        item = owner_client(env).get(f"{API}/{event.id}/attendees").json()["items"][0]
        assert item["is_checked_in"] is True and item["payment_status"] == "pending"  # admitted, but never shown as paid

    def test_online_buyers_still_pay_through_checkout_and_are_marked_paid(self, env):
        event = paid_event(env)
        assert checkout(env, event, "buyer@example.com").status_code == 201
        (order,) = orders(env, event)
        assert (order.status, order.payment_status, order.payment_provider) == ("confirmed", "confirmed", "marketplace")


# ===========================================================================
# 34-37. Check-in
# ===========================================================================


class TestCheckIn:
    def test_immediate_check_in_is_the_default(self, env):
        data = walk_in(env, body()).json()
        (row,) = regs(env)
        assert row.status == "attended" and row.checked_in_at is not None and str(row.checked_in_by) == env.owner["id"]
        assert data["check_in"]["performed"] and data["check_in"]["checked_in_at"]
        assert data["message"] == "Walk-in registered and checked in"

    def test_check_in_false_leaves_the_attendee_unchecked(self, env):
        data = walk_in(env, body(check_in=False)).json()
        (row,) = regs(env)
        assert row.status == "confirmed" and row.checked_in_at is None and row.checked_in_by is None
        assert data["check_in"] == {"requested": False, "performed": False, "checked_in_at": None, "reason": None}
        assert audits(env, "check_in") == [] and data["message"] == "Walk-in registered"

    def test_the_existing_event_level_check_in_service_is_reused_inside_the_transaction(self, env, monkeypatch):
        calls = []
        real = walk_in_service.check_in_service

        def spy(db, event_id, payload, current_user=None, *, commit=True):
            calls.append((payload.registration_id, payload.qr_code, payload.session_id, payload.method, commit))
            return real(db, event_id, payload, current_user, commit=commit)

        monkeypatch.setattr(walk_in_service, "check_in_service", spy)
        data = walk_in(env, body()).json()
        assert calls == [(UUID(data["registration"]["registration_id"]), None, None, "walk_in", False)]

    def test_the_result_is_the_same_state_a_normal_check_in_produces(self, env):
        normal = make_registration(env.db, env.event, "normal@example.com")
        assert owner_client(env).post(f"{API}/{env.event.id}/check-in", json={"registration_id": str(normal.id)}).status_code == 200
        walk_in(env, body("walker@example.com"))
        env.db.expire_all()
        by_email = {r.participant_email: r for r in env.db.query(EventRegistration)}
        a, b = by_email["normal@example.com"], by_email["walker@example.com"]
        assert (a.status, a.checked_out_at, a.session_id) == (b.status, b.checked_out_at, b.session_id) == ("attended", None, None)
        assert a.checked_in_by == b.checked_in_by
        normal_audit, walker_audit = (next(x for x in audits(env, "check_in") if x.after["participant_email"] == e) for e in ("normal@example.com", "walker@example.com"))
        assert set(normal_audit.after) == set(walker_audit.after) and walker_audit.changed_by == normal_audit.changed_by

    def test_the_check_in_audit_is_created(self, env):
        data = walk_in(env, body()).json()
        (audit,) = audits(env, "check_in")
        assert audit.event_id == env.event.id and audit.changed_by == env.owner["id"]
        assert audit.before == {"registration_id": data["registration"]["registration_id"], "status": "confirmed"}
        assert audit.after["status"] == "attended" and audit.after["method"] == "walk_in"

    def test_a_walk_in_can_be_checked_out_later_with_the_existing_flow(self, env):
        data = walk_in(env, body()).json()
        assert owner_client(env).post(f"{API}/{env.event.id}/check-out", json={"registration_id": data["registration"]["registration_id"]}).status_code == 200

    def test_the_operator_is_a_provider_when_a_provider_does_it(self, env):
        provider = staff_user(env.tenant, "provider")
        walk_in(env, body(), client=client_for(env.db, provider))
        (row,) = regs(env)
        assert str(row.checked_in_by) == provider["id"]
        assert audits(env, "walk_in_registration")[0].changed_by == provider["id"]


# ===========================================================================
# 38-42. Sessions
# ===========================================================================


class TestSessions:
    def test_a_walk_in_with_a_valid_session_is_checked_in_to_that_session(self, env):
        data = walk_in(env, body(session_id="keynote")).json()
        assert data["session_check_in"] == {"requested": True, "session_id": "keynote", "performed": True, "reason": None}
        (row,) = session_rows(env)
        assert row.session_id == "keynote" and row.event_id == env.event.id and str(row.checked_in_by) == env.owner["id"]
        states = {s["session_id"]: s["checked_in"] for s in data["registration"]["session_attendance"]}
        assert states == {"keynote": True, "workshop": False, "closing": False}

    def test_an_invalid_session_is_rejected_and_nothing_is_created(self, env):
        before = snapshot(env)
        resp = walk_in(env, body(session_id="nope"))
        assert resp.status_code == 400 and "session_id does not belong to this event" in resp.json()["detail"]
        assert snapshot(env) == before

    def test_a_session_of_another_event_is_rejected(self, env):
        before = snapshot(env)
        resp = walk_in(env, body(session_id="other-session"))
        assert resp.status_code == 400 and snapshot(env) == before

    def test_a_legacy_id_less_session_cannot_be_used(self, env):
        legacy = make_event(env.db, env.tenant, env.ent, sessions=[{"title": "No id"}])
        assert walk_in(env, body(session_id="No id"), event=legacy).status_code == 400
        assert walk_in(env, body(session_id="None"), event=legacy).status_code == 400
        assert regs(env, legacy) == []

    def test_the_phase_2_4_session_service_is_reused_inside_the_transaction(self, env, monkeypatch):
        calls = []
        real = walk_in_service.check_in_session_service

        def spy(db, event_id, session_id, payload, current_user=None, *, commit=True):
            calls.append((session_id, payload.registration_id, payload.method, commit))
            return real(db, event_id, session_id, payload, current_user, commit=commit)

        monkeypatch.setattr(walk_in_service, "check_in_session_service", spy)
        data = walk_in(env, body(session_id="workshop")).json()
        assert calls == [("workshop", UUID(data["registration"]["registration_id"]), "walk_in", False)]
        (audit,) = audits(env, "session_check_in")
        assert audit.after["session_id"] == "workshop" and audit.after["method"] == "walk_in" and audit.changed_by == env.owner["id"]

    def test_there_is_no_automatic_check_in_to_every_session(self, env):
        walk_in(env, body())
        assert session_rows(env) == []  # no session_id: the event-level check-in only
        walk_in(env, body(session_id="closing"))
        assert [r.session_id for r in session_rows(env)] == ["closing"]  # exactly the one asked for

    def test_the_legacy_registration_session_column_is_not_used(self, env):
        walk_in(env, body(session_id="keynote"))
        (row,) = regs(env)
        assert row.session_id is None

    def test_a_session_can_be_requested_without_the_event_level_check_in(self, env):
        data = walk_in(env, body(session_id="keynote", check_in=False)).json()
        assert data["check_in"]["performed"] is False and data["session_check_in"]["performed"] is True
        (row,) = regs(env)
        assert row.status == "confirmed"  # session attendance is independent of event-level check-in (Phase 2.4)


# ===========================================================================
# 43-45. Registration-form answers
# ===========================================================================

SIZE = {"id": "f-size", "source": "custom", "label": "T-shirt size", "renderer": "select", "required": True, "position": 1,
        "options": [{"label": "Small", "value": "S"}, {"label": "Medium", "value": "M"}]}
DIET = {"id": "f-diet", "source": "custom", "label": "Dietary notes", "renderer": "text", "required": False, "position": 2,
        "validation": {"min_length": 3, "max_length": 20}}
CODE = {"id": "f-code", "source": "custom", "label": "Badge code", "renderer": "text", "required": False, "position": 3,
        "validation": {"pattern": "^[A-Z]{3}$"}}
OFF = {"id": "f-off", "source": "custom", "label": "Retired question", "renderer": "text", "required": False, "position": 4, "is_enabled": False}


class TestCustomQuestions:
    @pytest.fixture
    def form_event(self, env):
        event = make_event(env.db, env.tenant, env.ent, capacity="20")
        add_form(env, event, [SIZE, DIET, CODE, OFF])
        return event

    def test_a_required_field_that_is_missing_rejects_the_walk_in(self, env, form_event):
        before = snapshot(env)
        resp = walk_in(env, body(custom_fields={"f-diet": "vegan"}), event=form_event)
        assert resp.status_code == 400 and resp.json()["detail"] == "Required custom field missing: T-shirt size"
        assert snapshot(env) == before

    def test_a_required_field_is_enforced_even_when_no_answers_are_sent_at_all(self, env, form_event):
        for payload in (body(), body(custom_fields={}), body(custom_fields=None)):
            resp = walk_in(env, payload, event=form_event)
            assert resp.status_code == 400 and "Required custom field missing: T-shirt size" in resp.json()["detail"]
        assert regs(env, form_event) == []

    def test_optional_fields_may_be_left_out(self, env, form_event):
        resp = walk_in(env, body(custom_fields={"f-size": "M"}), event=form_event)
        assert resp.status_code == 201, resp.text
        (row,) = regs(env, form_event)
        assert row.custom_fields == {"f-size": "M"}

    def test_valid_answers_are_stored_and_shown_with_their_labels(self, env, form_event):
        data = walk_in(env, body(custom_fields={"f-size": "S", "f-diet": "vegan", "f-code": "ABC"}), event=form_event).json()
        assert data["registration"]["custom_answers"] == [
            {"field_id": "f-size", "label": "T-shirt size", "value": "S"},
            {"field_id": "f-diet", "label": "Dietary notes", "value": "vegan"},
            {"field_id": "f-code", "label": "Badge code", "value": "ABC"},
        ]

    @pytest.mark.parametrize("answers, fragment", [
        ({"f-size": "XXL"}, "Invalid select value for 'T-shirt size'"),
        ({"f-size": "M", "f-diet": "no"}, "too short"),
        ({"f-size": "M", "f-diet": "x" * 21}, "too long"),
        ({"f-size": "M", "f-code": "abc"}, "format invalid"),
        ({"f-size": "M", "f-unknown": "sneaky"}, "Unknown custom field_id: f-unknown"),
        ({"f-size": "M", "f-off": "hidden"}, "Unknown custom field_id: f-off"),
        ({"f-size": ["M"]}, "expects string"),
        ({"f-size": "M", "f-diet": {"nested": "x"}}, "expects string"),
        ({"f-size": "M", "group_size": 4}, "Unknown custom field_id: group_size"),
        ({"f-size": "M", "group_members": []}, "Unknown custom field_id: group_members"),
        ({"f-size": None}, "is empty"),
    ])
    def test_invalid_answers_are_rejected_with_a_useful_message(self, env, form_event, answers, fragment):
        before = snapshot(env)
        resp = walk_in(env, body(custom_fields=answers), event=form_event)
        assert resp.status_code == 400 and fragment in resp.json()["detail"], resp.text
        assert snapshot(env) == before

    def test_the_group_bookkeeping_namespace_is_never_an_answer_even_if_a_form_field_uses_the_name(self, env):
        """A form field whose id collides with a bookkeeping key would be stored and then hidden from the attendee view."""
        event = make_event(env.db, env.tenant, env.ent)
        add_form(env, event, [{"id": "group_size", "source": "custom", "label": "Group size", "renderer": "text", "required": False, "position": 1}])
        resp = walk_in(env, body(custom_fields={"group_size": "4"}), event=event)
        assert resp.status_code == 400 and "Unknown custom field_id: group_size" in resp.json()["detail"]
        assert regs(env, event) == []

    def test_arbitrary_answers_are_never_stored_silently(self, env):
        resp = walk_in(env, body(custom_fields={"favourite_colour": "green"}))  # this event has no form at all
        assert resp.status_code == 400 and "Unknown custom field_id: favourite_colour" in resp.json()["detail"]
        assert regs(env) == []

    def test_an_event_without_a_form_needs_no_answers(self, env):
        assert walk_in(env, body()).status_code == 201
        assert walk_in(env, body(custom_fields={})).status_code == 201
        assert all(r.custom_fields == {} for r in regs(env))

    def test_the_answers_are_checked_by_the_existing_form_validator(self, env, form_event, monkeypatch):
        calls = []
        real = walk_in_service.validate_custom_values
        monkeypatch.setattr(walk_in_service, "validate_custom_values", lambda items, sections: (calls.append(items), real(items, sections))[1])
        walk_in(env, body(custom_fields={"f-size": "M"}), event=form_event)
        assert calls == [[{"field_id": "f-size", "value": "M"}]]

    def test_the_answers_are_validated_before_anything_is_locked_or_written(self, env, form_event):
        make_registration(env.db, form_event, "dup@example.com")
        assert walk_in(env, body("dup@example.com", custom_fields={"f-size": "XXL"}), event=form_event).status_code == 400  # not 409


# ===========================================================================
# 46-48. Audit + atomicity
# ===========================================================================


class TestAuditAndTransactions:
    def test_the_walk_in_audit_is_created(self, env):
        data = walk_in(env, body("audited@example.com", name="Audited", session_id="keynote")).json()
        (audit,) = audits(env, "walk_in_registration")
        assert audit.event_id == env.event.id and audit.changed_by == env.owner["id"] and audit.before is None
        assert audit.after["registration_id"] == data["registration"]["registration_id"]
        assert audit.after["participant_email"] == "audited@example.com" and audit.after["registration_source"] == "walk_in"
        assert audit.after["status"] == "confirmed" and audit.after["payment_status"] == "free" and audit.after["order_id"] is None
        assert audit.after["check_in_requested"] is True and audit.after["session_id"] == "keynote"
        assert [a.action for a in audits(env)] == ["walk_in_registration", "check_in", "session_check_in"]  # in the order it happened

    def test_a_paid_walk_in_audit_carries_the_order(self, env):
        event = paid_event(env)
        walk_in(env, body(ticket_type_id="general"), event=event)
        (audit,) = audits(env, "walk_in_registration")
        (order,) = orders(env, event)
        assert audit.after["order_id"] == str(order.id) and audit.after["amount"] == "500.0" and audit.after["payment_status"] == "pending"

    @pytest.mark.parametrize("failing", [
        lambda env: walk_in(env, body("dup@example.com")),
        lambda env: walk_in(env, body(session_id="nope")),
        lambda env: walk_in(env, body(ticket_type_id="nope")),
    ])
    def test_a_rejected_walk_in_leaves_no_orphan_audit_row(self, env, failing):
        make_registration(env.db, env.event, "dup@example.com")
        before = snapshot(env)
        assert failing(env).status_code >= 400
        assert snapshot(env) == before and audits(env) == []

    def test_a_full_event_leaves_no_orphan_audit_row(self, env):
        event = make_event(env.db, env.tenant, env.ent, capacity="1")
        make_registration(env.db, event, "a@example.com")
        assert walk_in(env, body(), event=event).status_code == 400
        assert audits(env) == []

    def test_a_failed_commit_rolls_back_the_registration_the_order_and_every_audit_row(self, env):
        event = paid_event(env, tickets=[{"id": "comp", "name": "Complimentary", "price": "0"}])

        def boom():
            env.db.flush()  # the registration, order and audit rows are really in the transaction when the commit fails
            raise RuntimeError("database went away")

        env.db.commit = boom
        try:
            resp = walk_in(env, body(ticket_type_id="comp", session_id="keynote"), event=event)
        finally:
            del env.db.commit
        assert resp.status_code == 500
        assert regs(env, event) == [] and orders(env, event) == [] and audits(env) == [] and session_rows(env) == []

    def test_a_failed_event_check_in_does_not_leave_a_confirmed_registration_behind(self, env, monkeypatch):
        def refuse(*args, **kwargs):
            raise HTTPException(status_code=400, detail="Cannot check-in: simulated failure")

        monkeypatch.setattr(walk_in_service, "check_in_service", refuse)
        resp = walk_in(env, body())
        assert resp.status_code == 400 and "simulated failure" in resp.json()["detail"]
        assert regs(env) == [] and audits(env) == []

    def test_a_failed_session_check_in_undoes_the_registration_and_the_event_check_in(self, env, monkeypatch):
        def refuse(*args, **kwargs):
            raise HTTPException(status_code=400, detail="Cannot check-in: simulated session failure")

        monkeypatch.setattr(walk_in_service, "check_in_session_service", refuse)
        resp = walk_in(env, body(session_id="keynote"))
        assert resp.status_code == 400
        assert regs(env) == [] and audits(env) == [] and session_rows(env) == []  # the event-level check-in was staged, then discarded

    def test_an_unexpected_error_is_rolled_back_too(self, env, monkeypatch):
        def explode(*args, **kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(walk_in_service, "check_in_service", explode)
        assert walk_in(env, body()).status_code == 500
        assert regs(env) == [] and audits(env) == []

    def test_everything_is_committed_once(self, env):
        paid = paid_event(env)
        commits = count_commits(env.db)
        walk_in(env, body(session_id="keynote"))  # registration + event check-in + session check-in + 3 audit rows
        assert len(commits) == 1
        walk_in(env, body(ticket_type_id="general"), event=paid)  # registration + order + audit
        assert len(commits) == 2

    def test_a_walk_in_that_fails_after_the_order_was_staged_leaves_no_order(self, env, monkeypatch):
        event = paid_event(env, tickets=[{"id": "comp", "name": "Complimentary", "price": "0"}])
        monkeypatch.setattr(walk_in_service, "check_in_service", lambda *a, **k: (_ for _ in ()).throw(HTTPException(400, "no")))
        assert walk_in(env, body(ticket_type_id="comp"), event=event).status_code == 400
        assert orders(env, event) == [] and regs(env, event) == []

    def test_notifications_are_best_effort_and_sent_only_for_a_complete_registration(self, env, monkeypatch):
        sent = []
        monkeypatch.setattr("app.services.notification_triggers.notify_registration_confirmation", lambda db, event, reg: sent.append(reg.participant_email))
        walk_in(env, body("free@example.com"))
        assert sent == ["free@example.com"]

        def broken(*args, **kwargs):
            raise RuntimeError("push service down")

        monkeypatch.setattr("app.services.notification_triggers.notify_registration_confirmation", broken)
        assert walk_in(env, body("still-ok@example.com")).status_code == 201  # a notification failure never fails the registration


# ===========================================================================
# 49-52. Attendee management + dashboard
# ===========================================================================


class TestAttendeeAndDashboardIntegration:
    @pytest.fixture
    def mixed(self, env):
        env.event.capacity = "50"
        env.db.commit()
        online = client_for(env.db, customer_user("online@example.com")).post(
            f"{API}/{env.event.id}/registrations", json={"participant_name": "Online", "participant_email": "online@example.com"}).json()
        legacy = make_registration(env.db, env.event, "legacy@example.com")  # created directly: NULL source, like every pre-existing row
        walked = walk_in(env, body("walked@example.com", name="Walked")).json()
        return SimpleNamespace(online=online, legacy=legacy, walked=walked)

    def unchecked_walk_in(self, env):
        return walk_in(env, body("unchecked@example.com", check_in=False)).json()

    def listing(self, env, **params):
        resp = owner_client(env).get(f"{API}/{env.event.id}/attendees", params={"page_size": 100, **params})
        assert resp.status_code == 200, resp.text
        return {i["participant_email"]: i for i in resp.json()["items"]}

    def test_sources_are_reported_and_null_is_online(self, env, mixed):
        items = self.listing(env)
        assert {e: i["registration_source"] for e, i in items.items()} == {
            "online@example.com": "online", "legacy@example.com": "online", "walked@example.com": "walk_in"}

    def test_the_source_filter(self, env, mixed):
        assert set(self.listing(env, source="walk_in")) == {"walked@example.com"}
        assert set(self.listing(env, source="online")) == {"online@example.com", "legacy@example.com"}  # NULL rows are online
        assert len(self.listing(env)) == 3
        assert owner_client(env).get(f"{API}/{env.event.id}/attendees", params={"source": "kiosk"}).status_code == 422

    def test_the_source_filter_combines_with_the_others(self, env, mixed):
        assert set(self.listing(env, source="walk_in", checked_in="true")) == {"walked@example.com"}
        assert self.listing(env, source="walk_in", checked_in="false") == {}
        assert set(self.listing(env, source="online", q="legacy")) == {"legacy@example.com"}

    def test_the_csv_export_has_a_source_column_and_honours_the_filter(self, env, mixed):
        resp = owner_client(env).get(f"{API}/{env.event.id}/registrations/export")
        rows = list(csv.reader(io.StringIO(resp.text)))
        source = rows[0].index("source")
        assert rows[0][:5] == ["id", "name", "email", "status", "qr_code"] and rows[0][source - 1] == "answers"  # everything before it is unchanged
        assert {r[2]: r[source] for r in rows[1:]} == {"online@example.com": "online", "legacy@example.com": "online", "walked@example.com": "walk_in"}
        filtered = list(csv.reader(io.StringIO(owner_client(env).get(f"{API}/{env.event.id}/registrations/export", params={"source": "walk_in"}).text)))
        assert [r[2] for r in filtered[1:]] == ["walked@example.com"]

    def test_the_dashboard_counts_walk_ins_normally_and_splits_them_out(self, env, mixed):
        registrations = dashboard(env)["registrations"]
        assert registrations == {"total": 3, "active": 3, "confirmed": 2, "attended": 1, "cancelled": 0, "no_show": 0, "other": 0,
                                 "online": 2, "walk_in": 1}
        assert dashboard(env)["capacity"]["seats_taken"] == 3

    def test_a_cancelled_walk_in_still_counts_as_a_walk_in_but_not_as_active(self, env, mixed):
        reg_id = self.unchecked_walk_in(env)["registration"]["registration_id"]
        assert owner_client(env).delete(f"{API}/{env.event.id}/registrations/{reg_id}").status_code == 200
        registrations = dashboard(env)["registrations"]
        assert (registrations["total"], registrations["active"], registrations["walk_in"], registrations["online"]) == (4, 3, 2, 2)
        assert dashboard(env)["capacity"]["seats_taken"] == 3  # the cancelled one released its seat

    def test_orders_and_revenue_stay_correct_around_paid_walk_ins(self, env):
        event = paid_event(env, capacity="10")
        assert checkout(env, event, "buyer@example.com", quantity=2).status_code == 201  # 1000, paid
        walk_in(env, body("pending@example.com", ticket_type_id="general"), event=event)  # 500, pending
        comp_event = paid_event(env, tickets=[{"id": "comp", "name": "Complimentary", "price": "0"}])
        walk_in(env, body(ticket_type_id="comp"), event=comp_event)
        walk_in(env, body(), event=env.event)  # a free walk-in: no order at all
        board = dashboard(env, event)
        assert board["orders"] == {"total": 2, "successful": 1, "pending": 1, "refund_requested": 0, "refunded": 0, "cancelled": 0, "failed": 0}
        assert board["revenue"]["total_revenue"] == 1000.0 and board["revenue"]["paid_orders"] == 1
        assert board["registrations"]["total"] == 2 and board["registrations"]["walk_in"] == 1
        assert board["capacity"]["seats_taken"] == 3  # 2 from the family order + the pending walk-in
        assert dashboard(env, comp_event)["orders"]["successful"] == 1 and dashboard(env)["orders"]["total"] == 0

    def test_a_pending_walk_in_shows_as_pending_in_the_list_and_never_as_revenue(self, env):
        event = paid_event(env)
        walk_in(env, body("pending@example.com", ticket_type_id="general"), event=event)
        (item,) = owner_client(env).get(f"{API}/{event.id}/attendees", params={"payment_status": "pending"}).json()["items"]
        assert item["participant_email"] == "pending@example.com" and item["registration_source"] == "walk_in"
        assert owner_client(env).get(f"{API}/{event.id}/attendees", params={"payment_status": "paid"}).json()["items"] == []

    def test_the_reports_stay_consistent(self, env, mixed):
        report = owner_client(env).get(f"{API}/{env.event.id}/reports", params={"type": "registration"}).json()["data"]
        assert report["total_registrations"] == 3 and report["by_status"] == {"confirmed": 2, "attended": 1}
        attendance = owner_client(env).get(f"{API}/{env.event.id}/attendance").json()
        assert (attendance["total_registered"], attendance["total_attended"]) == (3, 1)


# ===========================================================================
# OpenAPI
# ===========================================================================


class TestOpenApi:
    @pytest.fixture(scope="class")
    def spec(self):
        from app.main import app as fastapi_app

        return fastapi_app.openapi()

    PATH = "/api/v1/events/{event_id}/walk-in"

    def test_the_operation_is_documented_and_typed(self, spec):
        operation = spec["paths"][self.PATH]["post"]
        assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/EventWalkInRequest")
        assert operation["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith("/EventWalkInResponse")
        assert {"400", "403", "404", "409", "422"} <= set(operation["responses"])
        assert operation["summary"] and "Organizer only" in operation["description"] and "pending" in operation["description"]

    def test_the_request_schema_refuses_unknown_fields_and_documents_the_defaults(self, spec):
        schema = spec["components"]["schemas"]["EventWalkInRequest"]
        assert schema["additionalProperties"] is False and set(schema["required"]) == {"participant_name", "participant_email"}
        assert schema["properties"]["check_in"]["default"] is True
        assert {"ticket_type_id", "custom_fields", "session_id"} <= set(schema["properties"])
        assert not ({"tenant_id", "enterprise_id", "status", "qr_code", "registration_source"} & set(schema["properties"]))

    def test_the_response_schema_reuses_the_typed_attendee_view(self, spec):
        schema = spec["components"]["schemas"]["EventWalkInResponse"]
        assert schema["properties"]["registration"]["$ref"].endswith("/EventAttendeeResponse")
        assert {"message", "registration", "ticket", "payment", "check_in", "session_check_in"} <= set(schema["properties"])
        assert "registration_source" in spec["components"]["schemas"]["EventAttendeeResponse"]["properties"]

    def test_the_security_matches_the_other_organizer_routes(self, spec):
        paths = spec["paths"]
        assert paths[self.PATH]["post"].get("security") == paths["/api/v1/events/{event_id}/check-in"]["post"].get("security")
        assert paths[self.PATH]["post"].get("security")

    def test_the_new_query_parameter_is_documented(self, spec):
        for path in ("/api/v1/events/{event_id}/attendees", "/api/v1/events/{event_id}/registrations/export"):
            params = {p["name"]: p for p in spec["paths"][path]["get"]["parameters"]}
            assert "source" in params and params["source"]["description"]

    def test_existing_registration_and_checkout_contracts_are_unchanged(self, spec):
        register = spec["paths"]["/api/v1/events/{event_id}/registrations"]["post"]
        assert register["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/EventRegistrationCreate")
        checkout_op = spec["paths"]["/api/v1/events/{event_id}/checkout"]["post"]
        assert checkout_op["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith("/EventOrderResponse")
        assert "registration_source" not in spec["components"]["schemas"]["EventRegistrationCreate"]["properties"]
