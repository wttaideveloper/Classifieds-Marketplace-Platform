"""
Event Management Phase 2.3 — the Event dashboard.

Real (in-memory SQLite) database via tests/event_sql_support.py: every number is produced by the SQL the
endpoint emits, and the capacity figures are cross-checked against what registration, checkout and the
waitlist promotion actually accept.

Covers: shape + typing, registrations / attendance, capacity (no double counting, multi-quantity orders,
live waitlist offers, unlimited, overbooked, parity with the real flows), waitlist, orders, revenue (orders
only), event block (status / lifecycle_state / event_type / modules), read-only behaviour, statement counts,
the legacy ``type=revenue`` report, OpenAPI.

Run:
    pytest tests/test_event_phase_2_3_dashboard.py -v
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event as sa_event

from event_sql_support import (
    API,
    EventOrder,
    EventRegistration,
    EventWaitlist,
    client_for,
    customer_user,
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

TOP_LEVEL_KEYS = {"event", "registrations", "capacity", "attendance", "waitlist", "orders", "revenue", "sessions",
                  "generated_at"}  # sessions: Phase 2.4, additive
LONG_AGO = datetime(2020, 1, 1)  # orders older than the registration they paid for


@pytest.fixture
def env(monkeypatch):
    apply_async = silence_side_effects(monkeypatch)
    db = make_session()
    tenant = uuid4()
    ent = make_enterprise(db, tenant)
    ticket = paid_ticket("500", ticket_id="general")
    event = make_event(db, tenant, ent, pricing_type="paid", price="500", ticket_types=[ticket], capacity="10", currency="INR")
    yield SimpleNamespace(db=db, tenant=tenant, ent=ent, event=event, ticket=ticket, apply_async=apply_async,
                          owner=staff_user(tenant, "admin"))
    reset_overrides()
    db.close()


def dash(env, event=None):
    resp = client_for(env.db, env.owner).get(f"{API}/{(event or env.event).id}/dashboard")
    assert resp.status_code == 200, resp.text
    return resp.json()


def reg(env, email, status="confirmed", event=None, **kwargs):
    return make_registration(env.db, event or env.event, email, status=status, **kwargs)


def paid_registration(env, email, amount="500", quantity="1", status="confirmed", payment_status="confirmed", ticket="general", event=None):
    """A registration together with the order that paid for it (order first, like checkout)."""
    event = event or env.event
    order = make_order(env.db, event, email, ticket_type_id=ticket, quantity=quantity, amount=amount, status=status,
                       payment_status=payment_status, created_at=LONG_AGO)
    return reg(env, email, event=event, ticket_type_id=ticket), order


def checkout(env, email, quantity=1, event=None):
    return client_for(env.db, customer_user(email)).post(
        f"{API}/{(event or env.event).id}/checkout",
        json={"participant_name": email.split("@")[0], "participant_email": email, "ticket_type_id": "general", "quantity": quantity})


# ===========================================================================
# Shape
# ===========================================================================


class TestShape:
    def test_empty_event_dashboard(self, env):
        body = dash(env)
        assert set(body) == TOP_LEVEL_KEYS
        assert body["registrations"] == {"total": 0, "active": 0, "confirmed": 0, "attended": 0, "cancelled": 0, "no_show": 0, "other": 0}
        assert body["attendance"] == {"checked_in": 0, "not_checked_in": 0, "attendance_percentage": None}
        assert body["waitlist"] == {"total": 0, "waiting": 0, "payment_pending": 0, "promoted": 0, "expired": 0, "left": 0, "other": 0}
        assert body["orders"] == {"total": 0, "successful": 0, "pending": 0, "refund_requested": 0, "refunded": 0, "cancelled": 0, "failed": 0}
        assert body["capacity"] == {"capacity": 10, "unlimited": False, "seats_taken": 0, "seats_reserved": 0,
                                    "available_seats": 10, "is_full": False, "fill_percentage": 0.0}
        assert body["revenue"]["total_revenue"] == 0.0 and body["revenue"]["paid_orders"] == 0
        assert body["revenue"]["currency"] == "INR" and body["revenue"]["mixed_currency"] is False
        assert body["revenue"]["by_currency"] == [] and body["revenue"]["unparseable_orders"] == 0
        assert datetime.fromisoformat(body["generated_at"])

    def test_event_block(self, env):
        block = dash(env)["event"]
        assert block["id"] == str(env.event.id) and block["title"] == "Original title"
        assert block["status"] == "published" and block["lifecycle_state"] == "upcoming"
        assert block["pricing_type"] == "paid" and block["currency"] == "INR" and block["time_zone"] == "UTC"
        assert set(block["modules"]) == {"registration", "tickets", "sessions", "check_in", "online_meeting",
                                         "custom_questions", "meals", "accommodation"}

    def test_legacy_event_resolves_type_and_modules_and_configured_event_returns_them(self, env):
        legacy = dash(env)["event"]
        assert legacy["event_type"] == "other" and legacy["modules"]["tickets"] is True  # priced, so tickets is on
        configured = make_event(env.db, env.tenant, env.ent, event_type="camp",
                                modules={"registration": True, "tickets": False, "sessions": False, "check_in": True,
                                         "online_meeting": False, "custom_questions": False, "meals": True, "accommodation": True})
        block = dash(env, configured)["event"]
        assert block["event_type"] == "camp" and block["modules"]["meals"] is True and block["modules"]["tickets"] is False

    def test_lifecycle_state_is_the_existing_time_based_value_and_status_is_untouched(self, env):
        now = datetime.utcnow()
        ongoing = make_event(env.db, env.tenant, env.ent, start_date=now - timedelta(hours=1), end_date=now + timedelta(hours=1))
        finished = make_event(env.db, env.tenant, env.ent, start_date=now - timedelta(days=3), end_date=now - timedelta(days=2),
                              status="completed")
        assert dash(env, ongoing)["event"]["lifecycle_state"] == "ongoing"
        block = dash(env, finished)["event"]
        assert block["lifecycle_state"] == "finished" and block["status"] == "completed"

    def test_dashboard_does_not_enforce_or_change_modules(self, env):
        modules = {"registration": True, "tickets": False, "sessions": False, "check_in": False, "online_meeting": False,
                   "custom_questions": False, "meals": False, "accommodation": False}
        no_checkin = make_event(env.db, env.tenant, env.ent, event_type="webinar", modules=modules)
        reg(env, "a@example.com", status="attended", event=no_checkin)
        body = dash(env, no_checkin)
        assert body["event"]["modules"] == modules  # reported as configured ...
        assert body["attendance"]["checked_in"] == 1  # ... and nothing is switched off because of it


# ===========================================================================
# Registrations + attendance
# ===========================================================================


class TestRegistrationsAndAttendance:
    def test_counts_by_status(self, env):
        for i in range(3):
            reg(env, f"c{i}@example.com")
        for i in range(2):
            reg(env, f"a{i}@example.com", status="attended")
        reg(env, "x@example.com", status="cancelled")
        reg(env, "n@example.com", status="no_show")
        reg(env, "w@example.com", status="waiting_room")  # not a status the backend writes, still counted
        assert dash(env)["registrations"] == {"total": 8, "active": 5, "confirmed": 3, "attended": 2, "cancelled": 1, "no_show": 1, "other": 1}

    def test_attendance_percentage(self, env):
        reg(env, "a@example.com", status="attended")
        reg(env, "c1@example.com")
        reg(env, "c2@example.com")
        reg(env, "x@example.com", status="cancelled")  # a cancelled registration is not an expected attendee
        attendance = dash(env)["attendance"]
        assert attendance == {"checked_in": 1, "not_checked_in": 2, "attendance_percentage": 33.3}

    def test_everyone_checked_in_is_100_percent_and_only_cancelled_is_null(self, env):
        reg(env, "a@example.com", status="attended")
        assert dash(env)["attendance"]["attendance_percentage"] == 100.0
        other = make_event(env.db, env.tenant, env.ent)
        reg(env, "x@example.com", status="cancelled", event=other)
        assert dash(env, other)["attendance"]["attendance_percentage"] is None

    def test_counts_are_scoped_to_the_event(self, env):
        other = make_event(env.db, env.tenant, env.ent)
        reg(env, "mine@example.com")
        reg(env, "theirs@example.com", event=other)
        make_waitlist(env.db, other, "w@example.com")
        make_order(env.db, other, "theirs@example.com")
        body = dash(env)
        assert body["registrations"]["total"] == 1 and body["waitlist"]["total"] == 0 and body["orders"]["total"] == 0


# ===========================================================================
# Capacity
# ===========================================================================


class TestCapacity:
    def test_a_paid_registration_and_its_order_are_one_seat(self, env):
        paid_registration(env, "a@example.com")
        capacity = dash(env)["capacity"]
        assert capacity["seats_taken"] == 1 and capacity["available_seats"] == 9

    def test_free_registration_is_one_seat(self, env):
        free = make_event(env.db, env.tenant, env.ent, capacity="5")
        reg(env, "a@example.com", event=free)
        assert dash(env, free)["capacity"]["seats_taken"] == 1

    def test_a_multi_quantity_order_holds_all_its_seats_exactly_once(self, env):
        paid_registration(env, "family@example.com", amount="1500", quantity="3")
        paid_registration(env, "single@example.com")
        capacity = dash(env)["capacity"]
        assert capacity["seats_taken"] == 4  # 3 + 1, not (3+1) registrations-and-orders = 6
        assert capacity["fill_percentage"] == 40.0

    def test_only_confirmed_orders_hold_extra_seats(self, env):
        paid_registration(env, "refunded@example.com", amount="1500", quantity="3", status="refund_requested", payment_status="refund_requested")
        paid_registration(env, "cancelled@example.com", amount="1500", quantity="3", status="cancelled")
        assert dash(env)["capacity"]["seats_taken"] == 2  # just their two registrations

    def test_cancelled_registrations_do_not_hold_a_seat(self, env):
        reg(env, "x@example.com", status="cancelled")
        reg(env, "n@example.com", status="no_show")
        assert dash(env)["capacity"]["seats_taken"] == 0

    def test_attended_registrations_still_hold_a_seat(self, env):
        reg(env, "a@example.com", status="attended")
        assert dash(env)["capacity"]["seats_taken"] == 1

    def test_live_waitlist_payment_offers_reserve_seats(self, env):
        reg(env, "a@example.com")
        make_waitlist(env.db, env.event, "offer1@example.com", status="payment_pending", expires_at=datetime.utcnow() + timedelta(minutes=10))
        make_waitlist(env.db, env.event, "offer2@example.com", status="payment_pending", expires_at=None)  # no expiry = still live
        capacity = dash(env)["capacity"]
        assert capacity["seats_taken"] == 1 and capacity["seats_reserved"] == 2 and capacity["available_seats"] == 7

    def test_expired_offers_and_waiting_entries_reserve_nothing(self, env):
        make_waitlist(env.db, env.event, "late@example.com", status="payment_pending", expires_at=datetime.utcnow() - timedelta(minutes=1))
        make_waitlist(env.db, env.event, "queued@example.com", status="waiting")
        make_waitlist(env.db, env.event, "done@example.com", status="promoted")
        make_waitlist(env.db, env.event, "gone@example.com", status="left")
        assert dash(env)["capacity"]["seats_reserved"] == 0

    def test_full_event(self, env):
        env.event.capacity = "2"
        env.db.commit()
        reg(env, "a@example.com")
        reg(env, "b@example.com")
        capacity = dash(env)["capacity"]
        assert capacity["is_full"] is True and capacity["available_seats"] == 0 and capacity["fill_percentage"] == 100.0

    def test_reserved_seats_make_an_event_full_before_it_is_fill_100(self, env):
        env.event.capacity = "2"
        env.db.commit()
        reg(env, "a@example.com")
        make_waitlist(env.db, env.event, "offer@example.com", status="payment_pending", expires_at=datetime.utcnow() + timedelta(minutes=5))
        capacity = dash(env)["capacity"]
        assert capacity["is_full"] is True and capacity["available_seats"] == 0 and capacity["fill_percentage"] == 50.0

    def test_an_overbooked_event_never_reports_negative_availability(self, env):
        env.event.capacity = "1"
        env.db.commit()
        reg(env, "a@example.com")
        reg(env, "b@example.com")
        capacity = dash(env)["capacity"]
        assert capacity["available_seats"] == 0 and capacity["is_full"] is True and capacity["fill_percentage"] == 200.0

    @pytest.mark.parametrize("raw", [None, "", "  ", "abc", "inf", "nan"])
    def test_unusable_capacity_means_unlimited(self, env, raw):
        env.event.capacity = raw
        env.db.commit()
        reg(env, "a@example.com")
        assert dash(env)["capacity"] == {"capacity": None, "unlimited": True, "seats_taken": 1, "seats_reserved": 0,
                                         "available_seats": None, "is_full": None, "fill_percentage": None}

    def test_capacity_strings_are_read_like_the_seat_taking_paths(self, env):
        env.event.capacity = "12.0"
        env.db.commit()
        assert dash(env)["capacity"]["capacity"] == 12

    def test_zero_capacity_is_a_full_event_without_a_percentage(self, env):
        env.event.capacity = "0"
        env.db.commit()
        capacity = dash(env)["capacity"]
        assert capacity["capacity"] == 0 and capacity["is_full"] is True and capacity["fill_percentage"] is None

    def test_dashboard_capacity_agrees_with_what_checkout_accepts(self, env):
        env.event.capacity = "4"
        env.db.commit()
        assert checkout(env, "family@example.com", quantity=3).status_code == 201
        capacity = dash(env)["capacity"]
        assert capacity["seats_taken"] == 3 and capacity["available_seats"] == 1
        assert checkout(env, "pair@example.com", quantity=2).status_code == 400  # would need 2, only 1 left
        assert checkout(env, "solo@example.com", quantity=1).status_code == 201
        capacity = dash(env)["capacity"]
        assert capacity["is_full"] is True and capacity["available_seats"] == 0
        assert checkout(env, "late@example.com", quantity=1).status_code == 400

    def test_dashboard_capacity_agrees_with_free_registration(self, env):
        free = make_event(env.db, env.tenant, env.ent, capacity="2")
        client = client_for(env.db, customer_user("a@example.com"))
        for email, expected in (("a@example.com", 201), ("b@example.com", 201), ("c@example.com", 400)):
            resp = client.post(f"{API}/{free.id}/registrations", json={"participant_name": "P", "participant_email": email})
            assert resp.status_code == expected, resp.text
        assert dash(env, free)["capacity"]["is_full"] is True

    def test_dashboard_capacity_agrees_with_the_waitlist_promotion_rule(self, env):
        from app.services.event_service import _try_promote_from_waitlist

        env.event.capacity = "2"
        env.db.commit()
        reg(env, "a@example.com")
        make_waitlist(env.db, env.event, "offer@example.com", status="payment_pending",
                      expires_at=datetime.utcnow() + timedelta(minutes=10), created_at=datetime(2026, 1, 1))
        queued = make_waitlist(env.db, env.event, "queued@example.com", status="waiting", created_at=datetime(2026, 1, 2))

        assert dash(env)["capacity"]["available_seats"] == 0  # 1 taken + 1 reserved of 2
        _try_promote_from_waitlist(env.db, env.event.id, env.event)
        env.db.commit()
        env.db.refresh(queued)
        assert queued.status == "waiting"  # promotion agrees: no seat to offer

        env.db.query(EventWaitlist).filter_by(participant_email="offer@example.com").update(
            {"payment_offer_expires_at": datetime.utcnow() - timedelta(minutes=1)})
        env.db.commit()
        assert dash(env)["capacity"]["available_seats"] == 1
        _try_promote_from_waitlist(env.db, env.event.id, env.event)
        env.db.commit()
        env.db.refresh(queued)
        assert queued.status == "payment_pending"  # the released seat is offered onward


# ===========================================================================
# Waitlist
# ===========================================================================


class TestWaitlist:
    def test_counts_by_status(self, env):
        for status, n in (("waiting", 3), ("payment_pending", 2), ("promoted", 1), ("expired", 4), ("left", 1), ("mystery", 2)):
            for i in range(n):
                make_waitlist(env.db, env.event, f"{status}{i}@example.com", status=status,
                              expires_at=datetime.utcnow() + timedelta(minutes=5) if status == "payment_pending" else None)
        assert dash(env)["waitlist"] == {"total": 13, "waiting": 3, "payment_pending": 2, "promoted": 1, "expired": 4, "left": 1, "other": 2}

    def test_dashboard_is_read_only_and_never_promotes_or_offers(self, env):
        env.event.capacity = "5"
        env.db.commit()
        reg(env, "a@example.com")
        waiting = make_waitlist(env.db, env.event, "queued@example.com", status="waiting")  # a seat is free, but nothing runs
        before = (
            [(r.id, r.status) for r in env.db.query(EventRegistration).all()],
            [(w.id, w.status, w.payment_offer_expires_at) for w in env.db.query(EventWaitlist).all()],
            [(o.id, o.status) for o in env.db.query(EventOrder).all()],
        )
        for _ in range(3):
            dash(env)
        env.db.expire_all()
        after = (
            [(r.id, r.status) for r in env.db.query(EventRegistration).all()],
            [(w.id, w.status, w.payment_offer_expires_at) for w in env.db.query(EventWaitlist).all()],
            [(o.id, o.status) for o in env.db.query(EventOrder).all()],
        )
        assert before == after and env.db.get(EventWaitlist, waiting.id).status == "waiting"
        env.apply_async.assert_not_called()


# ===========================================================================
# Orders
# ===========================================================================


class TestOrders:
    def test_counts_by_derived_status(self, env):
        for i, (status, payment) in enumerate([
            ("confirmed", "confirmed"), ("confirmed", "confirmed"), ("completed", "confirmed"),
            ("confirmed", "pending"), ("refund_requested", "refund_requested"),
            ("refunded", "refunded"), ("cancelled", "confirmed"), ("confirmed", "failed"),
        ]):
            make_order(env.db, env.event, f"o{i}@example.com", status=status, payment_status=payment)
        assert dash(env)["orders"] == {"total": 8, "successful": 3, "pending": 1, "refund_requested": 1, "refunded": 1, "cancelled": 1, "failed": 1}

    def test_unrecognised_order_states_are_never_counted_as_successful(self, env):
        make_order(env.db, env.event, "a@example.com", status="weird", payment_status="whatever")
        make_order(env.db, env.event, "b@example.com", status="confirmed", payment_status="")
        orders = dash(env)["orders"]
        assert orders["successful"] == 0 and orders["pending"] == 2 and orders["total"] == 2


# ===========================================================================
# Revenue
# ===========================================================================


def revenue(env, event=None):
    return dash(env, event)["revenue"]


class TestRevenue:
    def test_revenue_is_the_sum_of_paid_order_totals(self, env):
        make_order(env.db, env.event, "a@example.com", amount="500")
        make_order(env.db, env.event, "b@example.com", amount="1500", quantity="3")
        body = revenue(env)
        assert body["total_revenue"] == 2000.0 and body["paid_orders"] == 2 and body["currency"] == "INR"
        assert body["by_currency"] == [{"currency": "INR", "total_revenue": 2000.0, "refunded_amount": 0.0,
                                        "pending_refund_amount": 0.0, "paid_orders": 2}]

    def test_revenue_ignores_ticket_prices_and_registration_counts(self, env):
        """The old report multiplied ticket prices by active registrations. Orders are the truth."""
        for i in range(4):
            reg(env, f"r{i}@example.com", ticket_type_id="general")  # 4 registrations of a 500 ticket, none paid
        assert revenue(env)["total_revenue"] == 0.0
        make_order(env.db, env.event, "d@example.com", amount="1250")  # e.g. a discounted price
        assert revenue(env)["total_revenue"] == 1250.0

    def test_amount_is_the_order_total_and_quantity_is_not_applied_again(self, env):
        assert checkout(env, "family@example.com", quantity=3).status_code == 201
        assert revenue(env)["total_revenue"] == 1500.0  # 3 x 500, once

    def test_refunds_and_cancellations_leave_revenue_and_are_reported_beside_it(self, env):
        make_order(env.db, env.event, "paid@example.com", amount="500")
        make_order(env.db, env.event, "refunded@example.com", amount="300", status="refunded", payment_status="refunded")
        make_order(env.db, env.event, "requested@example.com", amount="200", status="refund_requested", payment_status="refund_requested")
        make_order(env.db, env.event, "cancelled@example.com", amount="900", status="cancelled")
        make_order(env.db, env.event, "pending@example.com", amount="700", payment_status="pending")
        make_order(env.db, env.event, "failed@example.com", amount="600", payment_status="failed")
        body = revenue(env)
        assert body["total_revenue"] == 500.0 and body["paid_orders"] == 1
        assert body["refunded_amount"] == 300.0 and body["pending_refund_amount"] == 200.0

    def test_a_real_refund_moves_money_out_of_revenue(self, env):
        assert checkout(env, "buyer@example.com").status_code == 201
        assert revenue(env)["total_revenue"] == 500.0
        order = env.db.query(EventOrder).one()
        resp = client_for(env.db, env.owner).post(f"{API}/{env.event.id}/orders/{order.id}/refund", json={"reason": "changed mind"})
        assert resp.status_code == 200, resp.text
        body = dash(env)
        assert body["revenue"]["total_revenue"] == 0.0 and body["revenue"]["pending_refund_amount"] == 500.0
        assert body["orders"]["successful"] == 0 and body["orders"]["refund_requested"] == 1

    def test_free_event_has_no_revenue(self, env):
        free = make_event(env.db, env.tenant, env.ent, capacity="5")
        reg(env, "a@example.com", event=free)
        body = revenue(env, free)
        assert body["total_revenue"] == 0.0 and body["paid_orders"] == 0

    def test_decimal_sums_are_exact(self, env):
        for i, amount in enumerate(["0.1", "0.2", "59.699999999999996", "0.30000000000000004"]):
            make_order(env.db, env.event, f"d{i}@example.com", amount=amount)
        assert revenue(env)["total_revenue"] == 60.3  # 0.1 + 0.2 + 59.7 + 0.3 = 60.30, not float drift

    @pytest.mark.parametrize("bad", ["", "abc", "NaN", "Infinity", "-Infinity", None])
    def test_unreadable_amounts_are_excluded_and_counted(self, env, bad):
        make_order(env.db, env.event, "good@example.com", amount="100")
        make_order(env.db, env.event, "bad@example.com", amount=bad)
        body = revenue(env)
        assert body["total_revenue"] == 100.0 and body["unparseable_orders"] == 1
        assert dash(env)["orders"]["successful"] == 2  # still an order, still paid

    def test_one_non_default_currency_is_reported_as_is(self, env):
        make_order(env.db, env.event, "a@example.com", amount="10")
        env.db.query(EventOrder).update({"currency": "usd"})
        env.db.commit()
        body = revenue(env)
        assert body["currency"] == "USD" and body["total_revenue"] == 10.0 and body["mixed_currency"] is False

    def test_mixed_currencies_are_never_summed(self, env):
        make_order(env.db, env.event, "inr@example.com", amount="500")
        usd = make_order(env.db, env.event, "usd@example.com", amount="20")
        usd.currency = "USD"
        env.db.commit()
        body = revenue(env)
        assert body["mixed_currency"] is True
        assert body["total_revenue"] is None and body["refunded_amount"] is None and body["currency"] is None
        assert [(l["currency"], l["total_revenue"]) for l in body["by_currency"]] == [("INR", 500.0), ("USD", 20.0)]

    def test_revenue_is_scoped_to_the_event(self, env):
        other = make_event(env.db, env.tenant, env.ent, pricing_type="paid", price="10")
        make_order(env.db, other, "x@example.com", amount="9999")
        make_order(env.db, env.event, "a@example.com", amount="500")
        assert revenue(env)["total_revenue"] == 500.0

    def test_legacy_revenue_report_is_now_order_based_with_the_same_shape(self, env):
        for i in range(3):
            reg(env, f"r{i}@example.com", ticket_type_id="general")  # the old formula: 3 x 500 = 1500
        make_order(env.db, env.event, "a@example.com", ticket_type_id="general", amount="1000", quantity="2")
        resp = client_for(env.db, env.owner).get(f"{API}/{env.event.id}/reports", params={"type": "revenue"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["event_id"] == str(env.event.id) and body["type"] == "revenue"
        assert body["data"]["total_revenue"] == 1000.0 and body["data"]["currency"] == "INR"
        assert body["data"]["by_ticket_type"] == {"general": 1000.0}

    def test_legacy_revenue_report_with_no_orders(self, env):
        resp = client_for(env.db, env.owner).get(f"{API}/{env.event.id}/reports", params={"type": "revenue"})
        assert resp.json()["data"] == {"total_revenue": 0.0, "by_ticket_type": {}, "currency": "INR", "mixed_currency": False, "by_currency": []}

    def test_other_legacy_reports_are_unchanged(self, env):
        reg(env, "a@example.com")
        resp = client_for(env.db, env.owner).get(f"{API}/{env.event.id}/reports", params={"type": "registration"})
        assert resp.json()["data"] == {"total_registrations": 1, "by_status": {"confirmed": 1}, "by_ticket_type": {}}


# ===========================================================================
# Statement count
# ===========================================================================


class TestQueryCount:
    def populate(self, env, count, start=0):
        for i in range(start, start + count):
            paid_registration(env, f"n{i}@example.com", amount=str(100 + i % 7), ticket="general" if i % 2 else "vip")
            make_waitlist(env.db, env.event, f"w{i}@example.com", status=["waiting", "payment_pending", "left"][i % 3],
                          expires_at=datetime.utcnow() + timedelta(minutes=5))

    def selects(self, env):
        engine = env.db.get_bind()
        counter = {"n": 0}

        def count(conn, cursor, statement, *a):
            if statement.lstrip().upper().startswith("SELECT"):
                counter["n"] += 1

        sa_event.listen(engine, "before_cursor_execute", count)
        try:
            client_for(env.db, env.owner).get(f"{API}/{env.event.id}/dashboard")
        finally:
            sa_event.remove(engine, "before_cursor_execute", count)
        return counter["n"]

    def test_statement_count_does_not_grow_with_the_event(self, env):
        self.populate(env, 3)
        few = self.selects(env)
        self.populate(env, 150, start=3)
        assert self.selects(env) == few

    def test_large_event_totals_are_right(self, env):
        env.event.capacity = "1000"
        env.db.commit()
        self.populate(env, 200)
        body = dash(env)
        assert body["registrations"]["total"] == 200 and body["orders"]["successful"] == 200
        expected = sum(100 + i % 7 for i in range(200))
        assert body["revenue"]["total_revenue"] == float(expected) and body["revenue"]["paid_orders"] == 200
        assert body["waitlist"]["total"] == 200


# ===========================================================================
# OpenAPI
# ===========================================================================


class TestOpenApi:
    @pytest.fixture(scope="class")
    def spec(self):
        from app.main import app as fastapi_app

        return fastapi_app.openapi()

    def test_new_operations_are_documented_with_typed_responses(self, spec):
        paths = spec["paths"]
        dashboard = paths["/api/v1/events/{event_id}/dashboard"]["get"]
        assert dashboard["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/EventDashboardResponse")
        listing = paths["/api/v1/events/{event_id}/attendees"]["get"]
        assert listing["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/EventAttendeePaginatedResponse")
        detail = paths["/api/v1/events/{event_id}/registrations/{reg_id}"]["get"]
        assert detail["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/EventAttendeeResponse")

    def test_attendee_query_parameters_are_documented(self, spec):
        params = {p["name"]: p for p in spec["paths"]["/api/v1/events/{event_id}/attendees"]["get"]["parameters"]}
        assert {"q", "status", "ticket_type_id", "payment_status", "checked_in", "registered_from", "registered_to",
                "sort", "page", "page_size", "event_id"} <= set(params)
        assert all(params[name].get("description") for name in ("q", "status", "payment_status", "checked_in", "sort"))
        export = {p["name"] for p in spec["paths"]["/api/v1/events/{event_id}/registrations/export"]["get"]["parameters"]}
        assert {"q", "status", "payment_status", "checked_in"} <= export

    def test_new_routes_carry_the_same_security_as_the_existing_manager_routes(self, spec):
        paths = spec["paths"]
        legacy = paths["/api/v1/events/{event_id}/registrations"]["get"].get("security")
        for path in ("/dashboard", "/attendees", "/registrations/{reg_id}", "/registrations/export"):
            assert paths[f"/api/v1/events/{{event_id}}{path}"]["get"].get("security") == legacy
        assert legacy  # and that security is a real requirement, not an empty list
