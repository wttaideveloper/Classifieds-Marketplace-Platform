"""
Event Management Phase 2.3 — one definition of "what did this attendee pay".

The attendee list (filter + display), the dashboard order counts and the revenue figure all rest on
app/utils/event_payments.py and on the registration <-> order pairing. This module pins:

  * the truth table of ``derive_payment_status`` (Python) == ``payment_status_sql`` (the database filter)
    == the dashboard counts, for every combination of order status / payment status, including NULL and junk;
  * money parsing (never raises, never trusts NaN/inf);
  * the pairing: order-first checkout, rebooking after a cancellation, case-insensitive email, equal
    timestamps, other events / other people, and agreement with Phase 2.1's ``_linked_order_for_registration``
    for every registration that is still active.

Run:
    pytest tests/test_event_phase_2_3_payment_status.py -v
"""
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import product
from types import SimpleNamespace
from uuid import uuid4

import pytest

from event_sql_support import (
    API,
    EventOrder,
    EventRegistration,
    client_for,
    customer_user,
    make_enterprise,
    make_event,
    make_order,
    make_registration,
    make_session,
    paid_ticket,
    reset_overrides,
    silence_side_effects,
    staff_user,
)
from app.utils.event_payments import (
    PAYMENT_CANCELLED,
    PAYMENT_FAILED,
    PAYMENT_FREE,
    PAYMENT_PAID,
    PAYMENT_PENDING,
    PAYMENT_REFUND_REQUESTED,
    PAYMENT_REFUNDED,
    PAYMENT_UNPAID,
    derive_payment_status,
    money_to_float,
    parse_money,
)

LONG_AGO = datetime(2020, 1, 1)


@pytest.fixture
def env(monkeypatch):
    silence_side_effects(monkeypatch)
    db = make_session()
    tenant = uuid4()
    ent = make_enterprise(db, tenant)
    event = make_event(db, tenant, ent, pricing_type="paid", price="500", ticket_types=[paid_ticket("500", ticket_id="general")], capacity="500")
    yield SimpleNamespace(db=db, tenant=tenant, ent=ent, event=event, owner=staff_user(tenant, "admin"))
    reset_overrides()
    db.close()


def attendee_map(env, event=None, **params):
    client = client_for(env.db, env.owner)
    resp = client.get(f"{API}/{(event or env.event).id}/attendees", params={"page_size": 100, **params})
    assert resp.status_code == 200, resp.text
    return {item["participant_email"]: item for item in resp.json()["items"]}


# ===========================================================================
# Pure functions
# ===========================================================================


class TestDerivePaymentStatus:
    @pytest.mark.parametrize("status, payment, expected", [
        ("confirmed", "confirmed", PAYMENT_PAID),
        ("completed", "confirmed", PAYMENT_PAID),
        ("confirmed", "pending", PAYMENT_PENDING),
        ("confirmed", "failed", PAYMENT_FAILED),
        ("confirmed", "refund_requested", PAYMENT_REFUND_REQUESTED),
        ("refund_requested", "refund_requested", PAYMENT_REFUND_REQUESTED),
        ("refund_requested", "confirmed", PAYMENT_REFUND_REQUESTED),
        ("refunded", "refunded", PAYMENT_REFUNDED),
        ("confirmed", "refunded", PAYMENT_REFUNDED),
        ("refunded", "confirmed", PAYMENT_REFUNDED),
        ("cancelled", "confirmed", PAYMENT_CANCELLED),
        ("cancelled", "pending", PAYMENT_CANCELLED),
        ("cancelled", "failed", PAYMENT_CANCELLED),
        ("CONFIRMED", "Confirmed", PAYMENT_PAID),
        ("confirmed", None, PAYMENT_PENDING),  # unknown payment state is never "paid"
        (None, "confirmed", PAYMENT_PENDING),
        (None, None, PAYMENT_PENDING),
        ("weird", "confirmed", PAYMENT_PENDING),
        ("confirmed", "weird", PAYMENT_PENDING),
        ("refunded", "failed", PAYMENT_REFUNDED),  # precedence: refunded beats failed
        ("cancelled", "refund_requested", PAYMENT_CANCELLED),  # refund_requested -> cancelled leaves the payment column stale
        ("cancelled", "refunded", PAYMENT_REFUNDED),  # money went back: refunded is the more informative state
    ])
    def test_truth_table(self, status, payment, expected):
        assert derive_payment_status(status, payment) == expected

    def test_paid_is_the_only_settled_state(self):
        states = {derive_payment_status(s, p) for s, p in product(
            [None, "", "confirmed", "completed", "cancelled", "refund_requested", "refunded", "x"],
            [None, "", "confirmed", "pending", "failed", "refunded", "refund_requested", "x"])}
        assert states == {PAYMENT_PAID, PAYMENT_PENDING, PAYMENT_FAILED, PAYMENT_REFUND_REQUESTED, PAYMENT_REFUNDED, PAYMENT_CANCELLED}


class TestParseMoney:
    @pytest.mark.parametrize("raw, expected", [
        ("500", Decimal("500")), ("500.00", Decimal("500.00")), (" 12.5 ", Decimal("12.5")), (0, Decimal("0")),
        ("59.699999999999996", Decimal("59.699999999999996")), ("1e3", Decimal("1E+3")), ("-5", Decimal("-5")),
    ])
    def test_readable_amounts(self, raw, expected):
        assert parse_money(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "  ", "abc", "1,000", "NaN", "nan", "Infinity", "-inf", "sNaN", "1.2.3", "₹500"])
    def test_unreadable_amounts_are_none_and_never_raise(self, raw):
        assert parse_money(raw) is None

    def test_money_to_float_rounds_once_to_two_places(self):
        assert money_to_float(Decimal("59.699999999999996") + Decimal("0.1") + Decimal("0.2") + Decimal("0.30000000000000004")) == 60.3
        assert money_to_float(Decimal("0")) == 0.0
        assert money_to_float(Decimal("10.005")) in (10.0, 10.01)  # banker's vs half-up is not part of the contract


# ===========================================================================
# SQL filter == Python function == dashboard counts
# ===========================================================================

STATUSES = [None, "confirmed", "completed", "cancelled", "refund_requested", "refunded", "weird"]
PAYMENTS = [None, "confirmed", "pending", "failed", "refunded", "refund_requested", "weird"]


@pytest.fixture
def grid(env):
    """One registration + one older order per (status, payment_status) combination, real NULLs included."""
    combos = {}
    for i, (status, payment) in enumerate(product(STATUSES, PAYMENTS)):
        email = f"u{i:02d}@example.com"
        order = make_order(env.db, env.event, email, amount=str(10 + i), created_at=LONG_AGO)
        make_registration(env.db, env.event, email)
        env.db.query(EventOrder).filter_by(id=order.id).update({"status": status, "payment_status": payment})
        combos[email] = (status, payment)
    env.db.commit()
    return combos


class TestSqlMatchesPython:
    def test_display_matches_the_function_for_every_combination(self, env, grid):
        shown = attendee_map(env)
        assert len(shown) == len(grid) == 49
        for email, (status, payment) in grid.items():
            assert shown[email]["payment_status"] == derive_payment_status(status, payment), (status, payment)

    @pytest.mark.parametrize("wanted", [PAYMENT_PAID, PAYMENT_PENDING, PAYMENT_FAILED, PAYMENT_REFUND_REQUESTED,
                                         PAYMENT_REFUNDED, PAYMENT_CANCELLED])
    def test_sql_filter_returns_exactly_what_the_function_says(self, env, grid, wanted):
        expected = {email for email, (s, p) in grid.items() if derive_payment_status(s, p) == wanted}
        assert expected  # every state is reachable from the grid
        assert set(attendee_map(env, payment_status=wanted)) == expected

    def test_dashboard_counts_agree_with_the_same_function(self, env, grid):
        body = client_for(env.db, env.owner).get(f"{API}/{env.event.id}/dashboard").json()
        tally = {}
        for status, payment in grid.values():
            derived = derive_payment_status(status, payment)
            tally[derived] = tally.get(derived, 0) + 1
        orders = body["orders"]
        assert orders["total"] == 49
        assert orders["successful"] == tally.get(PAYMENT_PAID, 0)
        for key, derived in (("pending", PAYMENT_PENDING), ("refund_requested", PAYMENT_REFUND_REQUESTED),
                             ("refunded", PAYMENT_REFUNDED), ("cancelled", PAYMENT_CANCELLED), ("failed", PAYMENT_FAILED)):
            assert orders[key] == tally.get(derived, 0), key
        # and revenue counts exactly the orders the list calls paid / refunded / refund_requested
        by_email = {e: (10 + i) for i, e in enumerate(grid)}
        shown = attendee_map(env)
        paid_total = sum(by_email[e] for e, item in shown.items() if item["payment_status"] == PAYMENT_PAID)
        assert body["revenue"]["total_revenue"] == float(paid_total)
        refunded_total = sum(by_email[e] for e, item in shown.items() if item["payment_status"] == PAYMENT_REFUNDED)
        assert body["revenue"]["refunded_amount"] == float(refunded_total)

    def test_no_order_means_free_or_unpaid_by_event_pricing(self, env):
        free = make_event(env.db, env.tenant, env.ent)
        make_registration(env.db, env.event, "paid.event@example.com")
        make_registration(env.db, free, "free.event@example.com")
        assert attendee_map(env)["paid.event@example.com"]["payment_status"] == PAYMENT_UNPAID
        assert attendee_map(env, free)["free.event@example.com"]["payment_status"] == PAYMENT_FREE


# ===========================================================================
# Pairing
# ===========================================================================


class TestPairing:
    def test_checkout_pairs_its_own_order(self, env):
        client = client_for(env.db, customer_user("buyer@example.com"))
        order_id = client.post(f"{API}/{env.event.id}/checkout", json={
            "participant_name": "Buyer", "participant_email": "buyer@example.com", "ticket_type_id": "general", "quantity": 1}).json()["id"]
        assert attendee_map(env)["buyer@example.com"]["order_id"] == order_id

    def test_agrees_with_phase_2_1_pairing_for_every_active_registration(self, env):
        from app.services.event_service import _linked_order_for_registration

        for i in range(6):
            email = f"b{i}@example.com"
            resp = client_for(env.db, customer_user(email)).post(f"{API}/{env.event.id}/checkout", json={
                "participant_name": "B", "participant_email": email, "ticket_type_id": "general", "quantity": 1 + i % 3})
            assert resp.status_code == 201, resp.text
        shown = attendee_map(env)
        active = env.db.query(EventRegistration).filter(EventRegistration.status.in_(["confirmed", "attended"])).all()
        assert len(active) == 6
        for registration in active:
            assert shown[registration.participant_email]["order_id"] == str(_linked_order_for_registration(env.db, registration).id)

    def test_a_rebooking_pairs_each_registration_with_its_own_order(self, env):
        t0, t1 = datetime(2026, 1, 1, 9, 0), datetime(2026, 1, 1, 12, 0)
        first_order = make_order(env.db, env.event, "again@example.com", amount="500", created_at=t0)
        first_reg = make_registration(env.db, env.event, "again@example.com", created_at=t0, status="cancelled")
        first_order.status = first_order.payment_status = "refund_requested"
        second_order = make_order(env.db, env.event, "again@example.com", amount="700", created_at=t1)
        second_reg = make_registration(env.db, env.event, "again@example.com", created_at=t1)
        env.db.commit()

        client = client_for(env.db, env.owner)
        old = client.get(f"{API}/{env.event.id}/registrations/{first_reg.id}").json()
        new = client.get(f"{API}/{env.event.id}/registrations/{second_reg.id}").json()
        assert (old["order_id"], old["payment_status"], old["amount"]) == (str(first_order.id), "refund_requested", 500.0)
        assert (new["order_id"], new["payment_status"], new["amount"]) == (str(second_order.id), "paid", 700.0)

    def test_the_latest_order_before_the_registration_wins(self, env):
        make_order(env.db, env.event, "x@example.com", amount="100", status="cancelled", created_at=datetime(2026, 1, 1))
        newest = make_order(env.db, env.event, "x@example.com", amount="200", created_at=datetime(2026, 1, 2))
        make_registration(env.db, env.event, "x@example.com", created_at=datetime(2026, 1, 3))
        item = attendee_map(env)["x@example.com"]
        assert item["order_id"] == str(newest.id) and item["amount"] == 200.0 and item["payment_status"] == "paid"

    def test_equal_timestamps_pair(self, env):
        same = datetime(2026, 1, 1, 9, 0, 0)
        order = make_order(env.db, env.event, "tie@example.com", created_at=same)
        make_registration(env.db, env.event, "tie@example.com", created_at=same)
        assert attendee_map(env)["tie@example.com"]["order_id"] == str(order.id)

    def test_email_matching_ignores_case(self, env):
        order = make_order(env.db, env.event, "case@example.com", created_at=LONG_AGO)
        env.db.query(EventOrder).update({"participant_email": "CASE@Example.COM"})
        make_registration(env.db, env.event, "case@example.com")
        env.db.commit()
        assert attendee_map(env)["case@example.com"]["order_id"] == str(order.id)

    def test_orders_of_other_events_or_people_or_the_future_never_pair(self, env):
        other_event = make_event(env.db, env.tenant, env.ent, pricing_type="paid", price="1")
        make_order(env.db, other_event, "p@example.com", created_at=LONG_AGO)
        make_order(env.db, env.event, "someone.else@example.com", created_at=LONG_AGO)
        make_order(env.db, env.event, "p@example.com", created_at=datetime.utcnow() + timedelta(days=1))
        make_registration(env.db, env.event, "p@example.com")
        item = attendee_map(env)["p@example.com"]
        assert item["order_id"] is None and item["payment_status"] == PAYMENT_UNPAID

    def test_one_row_per_registration_however_many_orders_match(self, env):
        for day in range(1, 6):
            make_order(env.db, env.event, "many@example.com", created_at=datetime(2026, 1, day))
        make_registration(env.db, env.event, "many@example.com", created_at=datetime(2026, 2, 1))
        client = client_for(env.db, env.owner)
        body = client.get(f"{API}/{env.event.id}/attendees").json()
        assert body["pagination"]["total"] == 1 and len(body["items"]) == 1
        assert client.get(f"{API}/{env.event.id}/attendees", params={"payment_status": "paid"}).json()["pagination"]["total"] == 1

    def test_pairing_works_across_registration_status_including_cancelled(self, env):
        order = make_order(env.db, env.event, "c@example.com", created_at=LONG_AGO, status="cancelled")
        make_registration(env.db, env.event, "c@example.com", status="cancelled")
        item = attendee_map(env)["c@example.com"]
        assert item["order_id"] == str(order.id) and item["payment_status"] == PAYMENT_CANCELLED


# ===========================================================================
# Through the real order lifecycle (the states the backend actually writes)
# ===========================================================================


class TestRealOrderLifecycle:
    """Attendee list and dashboard follow an order through checkout -> refund request -> admin decision."""

    def buy(self, env, email="buyer@example.com", quantity=1):
        resp = client_for(env.db, customer_user(email)).post(f"{API}/{env.event.id}/checkout", json={
            "participant_name": "Buyer", "participant_email": email, "ticket_type_id": "general", "quantity": quantity})
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    def dashboard(self, env):
        return client_for(env.db, env.owner).get(f"{API}/{env.event.id}/dashboard").json()

    def request_refund(self, env, order_id):
        resp = client_for(env.db, env.owner).post(f"{API}/{env.event.id}/orders/{order_id}/refund", json={"reason": "changed mind"})
        assert resp.status_code == 200, resp.text

    def test_checkout_then_refund_request_then_approval(self, env):
        order_id = self.buy(env, quantity=2)
        assert attendee_map(env)["buyer@example.com"]["payment_status"] == PAYMENT_PAID
        assert self.dashboard(env)["revenue"]["total_revenue"] == 1000.0

        self.request_refund(env, order_id)
        item = attendee_map(env)["buyer@example.com"]
        assert item["payment_status"] == PAYMENT_REFUND_REQUESTED and item["registration_status"] == "confirmed"
        body = self.dashboard(env)
        assert body["revenue"]["total_revenue"] == 0.0 and body["revenue"]["pending_refund_amount"] == 1000.0

        resp = client_for(env.db, env.owner).post(f"{API}/{env.event.id}/orders/{order_id}/refund/approve", json={"action": "approve"})
        assert resp.status_code == 200, resp.text
        item = attendee_map(env)["buyer@example.com"]
        assert item["payment_status"] == PAYMENT_REFUNDED and item["registration_status"] == "cancelled"  # approval releases the seat
        body = self.dashboard(env)
        assert body["revenue"]["refunded_amount"] == 1000.0 and body["revenue"]["pending_refund_amount"] == 0.0
        assert body["orders"]["refunded"] == 1 and body["capacity"]["seats_taken"] == 0

    def test_a_rejected_refund_is_paid_again(self, env):
        order_id = self.buy(env)
        self.request_refund(env, order_id)
        resp = client_for(env.db, env.owner).post(f"{API}/{env.event.id}/orders/{order_id}/refund/approve", json={"action": "reject"})
        assert resp.status_code == 200, resp.text
        assert attendee_map(env)["buyer@example.com"]["payment_status"] == PAYMENT_PAID
        body = self.dashboard(env)
        assert body["revenue"]["total_revenue"] == 500.0 and body["revenue"]["pending_refund_amount"] == 0.0

    def test_refund_request_then_cancel_is_cancelled_not_a_pending_refund(self, env):
        """refund_requested -> cancelled leaves payment_status at "refund_requested"; the closed order must not
        read as a refund still waiting."""
        order_id = self.buy(env)
        self.request_refund(env, order_id)
        resp = client_for(env.db, env.owner).patch(f"{API}/{env.event.id}/orders/{order_id}/status", json={"status": "cancelled"})
        assert resp.status_code == 200, resp.text
        order = env.db.query(EventOrder).one()
        env.db.refresh(order)
        assert (order.status, order.payment_status) == ("cancelled", "refund_requested")  # the stale pair really occurs
        assert attendee_map(env)["buyer@example.com"]["payment_status"] == PAYMENT_CANCELLED
        body = self.dashboard(env)
        assert body["orders"]["cancelled"] == 1 and body["orders"]["refund_requested"] == 0
        assert body["revenue"]["pending_refund_amount"] == 0.0 and body["revenue"]["total_revenue"] == 0.0

    def test_confirmed_then_completed_is_still_paid(self, env):
        order_id = self.buy(env)
        resp = client_for(env.db, env.owner).patch(f"{API}/{env.event.id}/orders/{order_id}/status", json={"status": "completed"})
        assert resp.status_code == 200, resp.text
        assert attendee_map(env)["buyer@example.com"]["payment_status"] == PAYMENT_PAID
        assert self.dashboard(env)["revenue"]["total_revenue"] == 500.0
