"""
Event Management Phase 2.1 — paid flow, refund/cancellation consistency, waitlist promotion.

Real (in-memory SQLite) database via tests/event_sql_support.py so capacity counting, the
order<->registration pairing and FIFO promotion are exercised as SQL, not mocked.

Covers: paid-registration bypass, capacity double-count (+ quantity), refund/cancel releasing the
registration and seat, waitlist promotion (free, paid, ticket-type priced), offer expiry (the
confirmed TypeError), FIFO, wrong-event waitlist_id, and the additive /my/waitlist field.

Run:
    pytest tests/test_event_phase_2_1_payments_waitlist.py -v
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from event_sql_support import (
    API,
    Event,
    EventAudit,
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


@pytest.fixture
def env(monkeypatch):
    apply_async = silence_side_effects(monkeypatch)
    db = make_session()
    tenant = uuid4()
    ent = make_enterprise(db, tenant)
    yield SimpleNamespace(db=db, tenant=tenant, ent=ent, apply_async=apply_async, owner=staff_user(tenant, "admin"))
    reset_overrides()
    db.close()


def checkout_body(email, ticket, quantity=1, **extra):
    return {"participant_name": email.split("@")[0], "participant_email": email, "ticket_type_id": ticket["id"],
            "quantity": quantity, **extra}


def registrations(db, event, **filters):
    db.expire_all()
    return db.query(EventRegistration).filter_by(event_id=event.id, **filters).all()


def audits(db, action):
    db.expire_all()
    return db.query(EventAudit).filter(EventAudit.action == action).all()


def paid_event(env, capacity=None, ticket=None, **overrides):
    ticket = ticket or paid_ticket()
    event = make_event(env.db, env.tenant, env.ent, pricing_type="paid", price="500", ticket_types=[ticket],
                       capacity=capacity, **overrides)
    return event, ticket


# ===========================================================================
# Paid registration protection
# ===========================================================================


class TestPaidRegistrationProtection:
    def register(self, env, event, email="buyer@example.com"):
        return client_for(env.db, customer_user(email)).post(
            f"{API}/{event.id}/registrations", json={"participant_name": "Buyer", "participant_email": email})

    def test_paid_event_cannot_be_registered_for_free(self, env):
        event, _ = paid_event(env)
        resp = self.register(env, event)
        assert resp.status_code == 400
        assert "payment" in resp.json()["detail"].lower() and "checkout" in resp.json()["detail"].lower()
        assert registrations(env.db, event) == []

    def test_event_priced_only_through_ticket_types_is_paid(self, env):
        event = make_event(env.db, env.tenant, env.ent, pricing_type="paid", price=None, ticket_types=[paid_ticket("250")])
        assert self.register(env, event).status_code == 400

    def test_legacy_event_whose_pricing_type_defaulted_to_free_but_has_a_price_is_paid(self, env):
        event = make_event(env.db, env.tenant, env.ent, pricing_type="free", price="99", ticket_types=[])
        assert self.register(env, event).status_code == 400
        priced_tickets = make_event(env.db, env.tenant, env.ent, pricing_type="free", ticket_types=[paid_ticket("10")])
        assert self.register(env, priced_tickets, "other@example.com").status_code == 400

    def test_free_event_registration_still_works_and_keeps_its_response_shape(self, env):
        event = make_event(env.db, env.tenant, env.ent, capacity="5")
        resp = self.register(env, event, "Free.User@Example.com")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "confirmed" and body["qr_code"] and body["id"]  # what mobile reads
        assert body["participant_email"] == "free.user@example.com"

    def test_free_event_capacity_behaviour_is_unchanged(self, env):
        event = make_event(env.db, env.tenant, env.ent, capacity="2")
        assert self.register(env, event, "a@example.com").status_code == 201
        assert self.register(env, event, "b@example.com").status_code == 201
        full = self.register(env, event, "c@example.com")
        assert full.status_code == 400 and "capacity" in full.json()["detail"].lower()

    def test_paid_event_still_sells_through_checkout(self, env):
        event, ticket = paid_event(env)
        resp = client_for(env.db, customer_user("buyer@example.com")).post(f"{API}/{event.id}/checkout", json=checkout_body("buyer@example.com", ticket))
        assert resp.status_code == 201, resp.text
        assert len(registrations(env.db, event, status="confirmed")) == 1


# ===========================================================================
# Capacity row lock
# ===========================================================================


class TestCapacityRowLock:
    """`Event` was never imported in create_registration_service / create_waitlist_entry_service, so the
    NameError was swallowed and the FOR UPDATE row lock that serializes capacity checks never ran."""

    def spy_on_row_lock(self, monkeypatch):
        from sqlalchemy.orm import Query

        calls = []
        original = Query.with_for_update

        def spy(self, *args, **kwargs):
            calls.append(1)
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Query, "with_for_update", spy)
        return calls

    def test_free_registration_takes_the_event_row_lock(self, env, monkeypatch):
        locks = self.spy_on_row_lock(monkeypatch)
        event = make_event(env.db, env.tenant, env.ent, capacity="5")
        resp = client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{event.id}/registrations", json={"participant_name": "A", "participant_email": "a@example.com"})
        assert resp.status_code == 201 and locks

    def test_waitlist_join_takes_the_event_row_lock(self, env, monkeypatch):
        locks = self.spy_on_row_lock(monkeypatch)
        event = make_event(env.db, env.tenant, env.ent, capacity="1")
        make_registration(env.db, event, "holder@example.com")
        resp = client_for(env.db, customer_user("w@example.com")).post(
            f"{API}/{event.id}/waitlist", json={"participant_name": "W", "participant_email": "w@example.com"})
        assert resp.status_code == 201, resp.text
        assert locks


# ===========================================================================
# Checkout capacity: one purchase == one seat
# ===========================================================================


class TestCheckoutCapacity:
    def buy(self, env, event, ticket, email, quantity=1):
        return client_for(env.db, customer_user(email)).post(f"{API}/{event.id}/checkout", json=checkout_body(email, ticket, quantity))

    def test_capacity_one_paid_checkout_fills_the_ticket_and_rejects_the_next(self, env):
        event, ticket = paid_event(env, ticket=paid_ticket(capacity=1))
        assert self.buy(env, event, ticket, "first@example.com").status_code == 201
        second = self.buy(env, event, ticket, "second@example.com")
        assert second.status_code == 400 and "capacity" in second.json()["detail"].lower()
        assert env.db.query(EventOrder).count() == 1 and len(registrations(env.db, event)) == 1

    def test_one_order_does_not_consume_two_seats(self, env):
        """Regression: checkout used to count the order AND its registration, so capacity N sold out at N/2."""
        event, ticket = paid_event(env, ticket=paid_ticket(capacity=2))
        assert self.buy(env, event, ticket, "a@example.com").status_code == 201
        assert self.buy(env, event, ticket, "b@example.com").status_code == 201  # was rejected at "2/2" before the fix
        assert self.buy(env, event, ticket, "c@example.com").status_code == 400

    def test_event_level_capacity_counts_each_purchase_once(self, env):
        event, ticket = paid_event(env, capacity="3")
        for email in ("a@example.com", "b@example.com", "c@example.com"):
            assert self.buy(env, event, ticket, email).status_code == 201, email
        assert self.buy(env, event, ticket, "d@example.com").status_code == 400

    def test_multi_quantity_order_holds_all_of_its_seats(self, env):
        event, ticket = paid_event(env, capacity="3")
        assert self.buy(env, event, ticket, "family@example.com", quantity=2).status_code == 201
        # one registration was created for the order, but two seats are held:
        assert self.buy(env, event, ticket, "big@example.com", quantity=2).status_code == 400
        assert self.buy(env, event, ticket, "solo@example.com", quantity=1).status_code == 201
        assert self.buy(env, event, ticket, "late@example.com", quantity=1).status_code == 400

    def test_seat_accounting_ignores_cancelled_and_refunded_orders(self, env):
        from app.services.event_service import _seats_taken

        event, ticket = paid_event(env, capacity="5")
        make_registration(env.db, event, "live@example.com", ticket_type_id=ticket["id"])
        make_order(env.db, event, "live@example.com", ticket_type_id=ticket["id"])
        make_registration(env.db, event, "gone@example.com", status="cancelled", ticket_type_id=ticket["id"])
        make_order(env.db, event, "gone@example.com", ticket_type_id=ticket["id"], quantity="4", status="refunded", payment_status="refunded")
        make_registration(env.db, event, "free@example.com")  # free registration without an order
        assert _seats_taken(env.db, event.id) == 2
        assert _seats_taken(env.db, event.id, ticket["id"]) == 1

    def test_waitlist_join_gate_uses_the_same_single_count(self, env):
        """A purchase must not make an event look fuller than it is (the gate said 'full' at half capacity)."""
        event, ticket = paid_event(env, capacity="2")
        make_registration(env.db, event, "a@example.com", ticket_type_id=ticket["id"])
        make_order(env.db, event, "a@example.com", ticket_type_id=ticket["id"])
        resp = client_for(env.db, customer_user("w@example.com")).post(
            f"{API}/{event.id}/waitlist", json={"participant_name": "W", "participant_email": "w@example.com"})
        assert resp.status_code == 400 and "1 seat" in resp.json()["detail"]


# ===========================================================================
# waitlist_id scoping in checkout
# ===========================================================================


class TestCheckoutWaitlistScoping:
    def offer(self, env, event, email, **kw):
        return make_waitlist(env.db, event, email, status="payment_pending",
                             expires_at=kw.pop("expires_at", datetime.utcnow() + timedelta(minutes=10)), **kw)

    def test_offer_for_another_event_is_rejected(self, env):
        event_a, ticket_a = paid_event(env, capacity="1")
        event_b, ticket_b = paid_event(env, capacity="1")
        make_registration(env.db, event_b, "holder@example.com", ticket_type_id=ticket_b["id"])  # B is full
        offer_for_a = self.offer(env, event_a, "buyer@example.com")
        resp = client_for(env.db, customer_user("buyer@example.com")).post(
            f"{API}/{event_b.id}/checkout", json=checkout_body("buyer@example.com", ticket_b, waitlist_id=str(offer_for_a.id)))
        assert resp.status_code == 404
        env.db.expire_all()
        assert env.db.get(EventWaitlist, offer_for_a.id).status == "payment_pending"  # untouched
        assert env.db.query(EventOrder).count() == 0

    def test_own_events_offer_is_redeemed(self, env):
        event, ticket = paid_event(env, capacity="1")
        offer = self.offer(env, event, "buyer@example.com")
        resp = client_for(env.db, customer_user("buyer@example.com")).post(
            f"{API}/{event.id}/checkout", json=checkout_body("buyer@example.com", ticket, waitlist_id=str(offer.id)))
        assert resp.status_code == 201, resp.text
        env.db.expire_all()
        entry = env.db.get(EventWaitlist, offer.id)
        assert entry.status == "promoted" and entry.registration_id is not None

    def test_offer_of_another_user_is_still_rejected(self, env):
        event, ticket = paid_event(env, capacity="1")
        offer = self.offer(env, event, "rightful@example.com")
        resp = client_for(env.db, customer_user("thief@example.com")).post(
            f"{API}/{event.id}/checkout", json=checkout_body("thief@example.com", ticket, waitlist_id=str(offer.id)))
        assert resp.status_code == 403

    def test_expired_offer_is_rejected(self, env):
        event, ticket = paid_event(env, capacity="1")
        offer = self.offer(env, event, "late@example.com", expires_at=datetime.utcnow() - timedelta(minutes=1))
        resp = client_for(env.db, customer_user("late@example.com")).post(
            f"{API}/{event.id}/checkout", json=checkout_body("late@example.com", ticket, waitlist_id=str(offer.id)))
        assert resp.status_code == 400


# ===========================================================================
# Refund / cancellation consistency
# ===========================================================================


@pytest.fixture
def sold(env):
    """A paid event, capacity 1, sold to victim (order + registration), with someone waiting."""
    event, ticket = paid_event(env, capacity="1")
    reg = make_registration(env.db, event, "victim@example.com", ticket_type_id=ticket["id"])
    order = make_order(env.db, event, "victim@example.com", ticket_type_id=ticket["id"])
    return SimpleNamespace(event=event, ticket=ticket, reg=reg, order=order)


class TestRefundReleasesTheRegistration:
    def request_refund(self, env, sold):
        return client_for(env.db, customer_user("victim@example.com")).post(
            f"{API}/{sold.event.id}/registrations/{sold.order.id}/refund", json={"reason": "cannot attend"})

    def approve(self, env, sold, action="approve"):
        return client_for(env.db, env.owner).post(
            f"{API}/{sold.event.id}/orders/{sold.order.id}/refund/approve", json={"action": action})

    def test_refund_request_alone_does_not_cancel_anything(self, env, sold):
        assert self.request_refund(env, sold).status_code == 200
        env.db.expire_all()
        assert env.db.get(EventOrder, sold.order.id).status == "refund_requested"
        assert env.db.get(EventRegistration, sold.reg.id).status == "confirmed"

    def test_approved_refund_cancels_the_registration_frees_the_seat_and_invalidates_the_qr(self, env, sold):
        from app.services.event_service import _seats_taken

        self.request_refund(env, sold)
        assert _seats_taken(env.db, sold.event.id) == 1
        resp = self.approve(env, sold)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "refunded" and resp.json()["payment_status"] == "refunded"
        env.db.expire_all()
        order, reg = env.db.get(EventOrder, sold.order.id), env.db.get(EventRegistration, sold.reg.id)
        assert (order.status, order.payment_status) == ("refunded", "refunded")
        assert reg.status == "cancelled"
        assert _seats_taken(env.db, sold.event.id) == 0
        # the QR no longer works at the door
        staff = client_for(env.db, env.owner)
        assert staff.post(f"{API}/{sold.event.id}/check-in", json={"qr_code": sold.reg.qr_code}).status_code == 400
        assert staff.post(f"{API}/{sold.event.id}/validate-qr", json={"qr_code": sold.reg.qr_code}).json()["valid"] is False

    def test_approval_is_audited_with_the_actor_in_the_same_transaction(self, env, sold):
        self.request_refund(env, sold)
        self.approve(env, sold)
        refund = audits(env.db, "refund")
        assert {a.after["status"] for a in refund} == {"refund_requested", "refunded"}
        assert audits(env.db, "registration_cancel")[0].changed_by == env.owner["id"]

    def test_refund_approval_promotes_the_waitlist(self, env, sold, monkeypatch):
        waiting = make_waitlist(env.db, sold.event, "next@example.com")
        self.request_refund(env, sold)
        self.approve(env, sold)
        env.db.expire_all()
        entry = env.db.get(EventWaitlist, waiting.id)
        assert entry.status == "payment_pending"  # the event is paid: a 15 minute offer, not a free seat
        assert entry.payment_offer_expires_at > datetime.utcnow()
        env.apply_async.assert_called_once()

    def test_attended_participant_cannot_be_refunded(self, env, sold):
        self.request_refund(env, sold)
        env.db.get(EventRegistration, sold.reg.id).status = "attended"
        env.db.commit()
        resp = self.approve(env, sold)
        assert resp.status_code == 400 and "attended" in resp.json()["detail"]
        env.db.expire_all()
        assert env.db.get(EventOrder, sold.order.id).status == "refund_requested"  # nothing half-applied
        assert env.db.get(EventRegistration, sold.reg.id).status == "attended"

    def test_rejected_refund_leaves_the_registration_valid(self, env, sold):
        self.request_refund(env, sold)
        assert self.approve(env, sold, "reject").status_code == 200
        env.db.expire_all()
        assert env.db.get(EventOrder, sold.order.id).status == "confirmed"
        assert env.db.get(EventRegistration, sold.reg.id).status == "confirmed"
        assert audits(env.db, "refund")[-1].after["decision"] == "rejected"

    def test_order_status_route_cannot_refund_without_releasing_the_registration(self, env, sold):
        self.request_refund(env, sold)
        resp = client_for(env.db, env.owner).patch(f"{API}/{sold.event.id}/orders/{sold.order.id}/status", json={"status": "refunded"})
        assert resp.status_code == 200, resp.text
        env.db.expire_all()
        order = env.db.get(EventOrder, sold.order.id)
        assert (order.status, order.payment_status) == ("refunded", "refunded")  # financially accurate
        assert env.db.get(EventRegistration, sold.reg.id).status == "cancelled"
        assert audits(env.db, "order_status")

    def test_cancelling_an_order_releases_the_registration(self, env, sold):
        resp = client_for(env.db, env.owner).patch(f"{API}/{sold.event.id}/orders/{sold.order.id}/status", json={"status": "cancelled"})
        assert resp.status_code == 200
        env.db.expire_all()
        assert env.db.get(EventRegistration, sold.reg.id).status == "cancelled"

    def test_completing_an_order_does_not_touch_the_registration(self, env, sold):
        client_for(env.db, env.owner).patch(f"{API}/{sold.event.id}/orders/{sold.order.id}/status", json={"status": "completed"})
        env.db.expire_all()
        assert env.db.get(EventRegistration, sold.reg.id).status == "confirmed"

    def test_order_cannot_be_cancelled_after_attendance(self, env, sold):
        env.db.get(EventRegistration, sold.reg.id).status = "attended"
        env.db.commit()
        resp = client_for(env.db, env.owner).patch(f"{API}/{sold.event.id}/orders/{sold.order.id}/status", json={"status": "cancelled"})
        assert resp.status_code == 400
        env.db.expire_all()
        assert env.db.get(EventOrder, sold.order.id).status == "confirmed"

    def test_registration_path_refund_of_a_paid_registration_records_it_on_the_order(self, env, sold):
        resp = client_for(env.db, customer_user("victim@example.com")).post(
            f"{API}/{sold.event.id}/registrations/{sold.reg.id}/refund", json={"reason": "changed my mind"})
        assert resp.status_code == 200
        assert resp.json() == {"message": "Refund requested", "registration_id": str(sold.reg.id), "status": "cancelled"}  # shape unchanged
        env.db.expire_all()
        assert env.db.get(EventRegistration, sold.reg.id).status == "cancelled"
        order = env.db.get(EventOrder, sold.order.id)
        assert (order.status, order.refund_reason) == ("refund_requested", "changed my mind")  # no longer left looking settled

    def test_customer_cancellation_frees_the_seat_but_does_not_invent_a_refund(self, env, sold):
        resp = client_for(env.db, customer_user("victim@example.com")).delete(f"{API}/{sold.event.id}/registrations/{sold.reg.id}")
        assert resp.status_code == 200 and resp.json() == {"message": "Registration cancelled"}
        env.db.expire_all()
        assert env.db.get(EventRegistration, sold.reg.id).status == "cancelled"
        assert env.db.get(EventOrder, sold.order.id).status == "confirmed"
        assert audits(env.db, "registration_cancel")[0].changed_by

    def test_an_old_refunded_order_never_blocks_a_new_valid_registration(self, env):
        """The order<->registration pairing is by event+email+time, so history must not poison a re-purchase."""
        event, ticket = paid_event(env, capacity="5")
        old = datetime.utcnow() - timedelta(days=2)
        make_registration(env.db, event, "again@example.com", status="cancelled", ticket_type_id=ticket["id"], created_at=old)
        make_order(env.db, event, "again@example.com", ticket_type_id=ticket["id"], status="refunded", payment_status="refunded", created_at=old)
        new_reg = make_registration(env.db, event, "again@example.com", ticket_type_id=ticket["id"])
        make_order(env.db, event, "again@example.com", ticket_type_id=ticket["id"], created_at=datetime.utcnow() - timedelta(seconds=1))
        resp = client_for(env.db, env.owner).post(f"{API}/{event.id}/check-in", json={"registration_id": str(new_reg.id)})
        assert resp.status_code == 200, resp.text


# ===========================================================================
# Waitlist promotion / expiration
# ===========================================================================


class TestWaitlistPromotion:
    def cancel(self, env, event, reg_email):
        reg = env.db.query(EventRegistration).filter_by(event_id=event.id, participant_email=reg_email).one()
        return client_for(env.db, customer_user(reg_email)).delete(f"{API}/{event.id}/registrations/{reg.id}")

    def test_free_event_promotes_the_first_person_into_a_confirmed_registration(self, env):
        event = make_event(env.db, env.tenant, env.ent, capacity="1")
        make_registration(env.db, event, "holder@example.com")
        first = make_waitlist(env.db, event, "first@example.com", created_at=datetime.utcnow() - timedelta(hours=2))
        second = make_waitlist(env.db, event, "second@example.com", created_at=datetime.utcnow() - timedelta(hours=1))
        assert self.cancel(env, event, "holder@example.com").status_code == 200
        env.db.expire_all()
        promoted = env.db.get(EventWaitlist, first.id)
        assert promoted.status == "promoted" and promoted.registration_id is not None
        reg = env.db.get(EventRegistration, promoted.registration_id)
        assert (reg.participant_email, reg.status) == ("first@example.com", "confirmed") and reg.qr_code
        assert env.db.get(EventWaitlist, second.id).status == "waiting"  # FIFO: only one seat opened
        assert audits(env.db, "waitlist_promoted")[0].changed_by == "system:waitlist"
        env.apply_async.assert_not_called()  # free events have no payment offer

    def test_paid_event_offers_the_seat_for_fifteen_minutes(self, env):
        event, ticket = paid_event(env, capacity="1")
        make_registration(env.db, event, "holder@example.com", ticket_type_id=ticket["id"])
        make_order(env.db, event, "holder@example.com", ticket_type_id=ticket["id"])
        waiting = make_waitlist(env.db, event, "next@example.com")
        assert self.cancel(env, event, "holder@example.com").status_code == 200
        env.db.expire_all()
        entry = env.db.get(EventWaitlist, waiting.id)
        assert entry.status == "payment_pending"
        assert timedelta(minutes=14) < entry.payment_offer_expires_at - datetime.utcnow() <= timedelta(minutes=15)
        assert entry.registration_id is None and registrations(env.db, event, participant_email="next@example.com") == []
        env.apply_async.assert_called_once_with(args=[str(entry.id)], countdown=15 * 60)
        assert audits(env.db, "waitlist_offer")

    @pytest.mark.parametrize("pricing_type,price", [("paid", None), ("free", None)], ids=["paid-no-top-level-price", "legacy-pricing-type-free"])
    def test_event_priced_only_by_ticket_types_gets_a_payment_offer_not_a_free_seat(self, env, pricing_type, price):
        """Regression: only event.price was inspected, so ticket-type-priced events were promoted as FREE."""
        event = make_event(env.db, env.tenant, env.ent, pricing_type=pricing_type, price=price,
                           ticket_types=[paid_ticket("250")], capacity="1")
        make_registration(env.db, event, "holder@example.com")
        waiting = make_waitlist(env.db, event, "next@example.com")
        self.cancel(env, event, "holder@example.com")
        env.db.expire_all()
        assert env.db.get(EventWaitlist, waiting.id).status == "payment_pending"
        assert registrations(env.db, event, participant_email="next@example.com") == []

    def test_no_waitlist_means_no_error_and_the_seat_stays_open(self, env):
        event = make_event(env.db, env.tenant, env.ent, capacity="1")
        make_registration(env.db, event, "holder@example.com")
        assert self.cancel(env, event, "holder@example.com").status_code == 200
        assert registrations(env.db, event, status="confirmed") == []

    def test_unlimited_events_never_promote(self, env):
        event = make_event(env.db, env.tenant, env.ent, capacity=None)
        make_registration(env.db, event, "holder@example.com")
        waiting = make_waitlist(env.db, event, "next@example.com")
        self.cancel(env, event, "holder@example.com")
        env.db.expire_all()
        assert env.db.get(EventWaitlist, waiting.id).status == "waiting"

    def test_cancelled_or_suspended_events_do_not_promote(self, env):
        event = make_event(env.db, env.tenant, env.ent, capacity="1")
        make_registration(env.db, event, "holder@example.com")
        waiting = make_waitlist(env.db, event, "next@example.com")
        event.status = "suspended"
        env.db.commit()
        self.cancel(env, event, "holder@example.com")
        env.db.expire_all()
        assert env.db.get(EventWaitlist, waiting.id).status == "waiting"

    def test_a_live_payment_offer_holds_the_seat(self, env):
        """Repeated triggers must never offer more seats than exist."""
        from app.services.event_service import _try_promote_from_waitlist

        event, _ = paid_event(env, capacity="1")
        held = make_waitlist(env.db, event, "holder@example.com", status="payment_pending", expires_at=datetime.utcnow() + timedelta(minutes=10))
        queued = make_waitlist(env.db, event, "queued@example.com")
        _try_promote_from_waitlist(env.db, event.id, event)
        env.db.commit()
        env.db.expire_all()
        assert env.db.get(EventWaitlist, queued.id).status == "waiting"
        # ...but once that offer has lapsed the seat is free again
        env.db.get(EventWaitlist, held.id).payment_offer_expires_at = datetime.utcnow() - timedelta(minutes=1)
        env.db.commit()
        _try_promote_from_waitlist(env.db, event.id, event)
        env.db.commit()
        env.db.expire_all()
        assert env.db.get(EventWaitlist, queued.id).status == "payment_pending"

    def test_promotion_signature_accepts_the_two_argument_call_the_task_used_to_make(self, env):
        from app.services.event_service import _try_promote_from_waitlist

        event = make_event(env.db, env.tenant, env.ent, capacity="1")
        waiting = make_waitlist(env.db, event, "next@example.com")
        _try_promote_from_waitlist(env.db, event.id)  # event=None: it loads the event itself
        env.db.commit()
        env.db.expire_all()
        assert env.db.get(EventWaitlist, waiting.id).status == "promoted"

    def test_my_waitlist_exposes_the_offer_expiry_for_mobile(self, env):
        event, _ = paid_event(env, capacity="1")
        expires = datetime.utcnow() + timedelta(minutes=9)
        make_waitlist(env.db, event, "me@example.com", status="payment_pending", expires_at=expires)
        body = client_for(env.db, customer_user("Me@Example.com")).get(f"{API}/my/waitlist").json()
        assert body[0]["status"] == "payment_pending"
        assert body[0]["payment_offer_expires_at"].startswith(expires.strftime("%Y-%m-%dT%H:%M"))


class TestWaitlistOfferExpiryTask:
    """expire_waitlist_offer_task used to raise TypeError (missing `event`), so nobody was promoted after an expiry."""

    def run_task(self, env, monkeypatch, waitlist_id):
        from app.tasks.event_tasks import expire_waitlist_offer_task

        monkeypatch.setattr("app.db.database.SessionLocal", lambda: env.db)
        return expire_waitlist_offer_task.run(str(waitlist_id))  # (the task closes env.db when it finishes)

    def test_expiry_releases_the_seat_and_offers_it_to_the_next_person_in_line(self, env, monkeypatch):
        event, _ = paid_event(env, capacity="1")
        lapsed = make_waitlist(env.db, event, "slow@example.com", status="payment_pending",
                               expires_at=datetime.utcnow() - timedelta(minutes=1), created_at=datetime.utcnow() - timedelta(hours=3))
        next_up = make_waitlist(env.db, event, "next@example.com", created_at=datetime.utcnow() - timedelta(hours=2))
        later = make_waitlist(env.db, event, "later@example.com", created_at=datetime.utcnow() - timedelta(hours=1))
        lapsed_id, next_id, later_id = lapsed.id, next_up.id, later.id  # the task closes the session it is given
        result = self.run_task(env, monkeypatch, lapsed_id)
        assert result == {"status": "expired"}
        env.db.expire_all()
        assert env.db.get(EventWaitlist, lapsed_id).status == "expired"
        assert env.db.get(EventWaitlist, next_id).status == "payment_pending"  # FIFO
        assert env.db.get(EventWaitlist, later_id).status == "waiting"
        assert [a.changed_by for a in audits(env.db, "waitlist_expired")] == ["system:waitlist"]
        env.apply_async.assert_called_once()  # the next offer also gets its own expiry timer

    def test_expiry_with_nobody_waiting_is_a_clean_no_op(self, env, monkeypatch):
        event, _ = paid_event(env, capacity="1")
        lapsed = make_waitlist(env.db, event, "slow@example.com", status="payment_pending", expires_at=datetime.utcnow() - timedelta(minutes=1))
        assert self.run_task(env, monkeypatch, lapsed.id) == {"status": "expired"}

    def test_offer_that_was_already_redeemed_is_left_alone(self, env, monkeypatch):
        event, _ = paid_event(env, capacity="1")
        done = make_waitlist(env.db, event, "done@example.com", status="promoted")
        assert self.run_task(env, monkeypatch, done.id) == {"status": "already_processed"}

    def test_offer_that_has_not_expired_yet_is_left_alone(self, env, monkeypatch):
        event, _ = paid_event(env, capacity="1")
        live = make_waitlist(env.db, event, "live@example.com", status="payment_pending", expires_at=datetime.utcnow() + timedelta(minutes=5))
        assert self.run_task(env, monkeypatch, live.id) == {"status": "not_yet_expired"}
        env.db.expire_all()
        assert env.db.get(EventWaitlist, live.id).status == "payment_pending"

    def test_expiry_of_a_free_events_stale_offer_promotes_directly(self, env, monkeypatch):
        event = make_event(env.db, env.tenant, env.ent, capacity="1")
        lapsed = make_waitlist(env.db, event, "slow@example.com", status="payment_pending", expires_at=datetime.utcnow() - timedelta(minutes=1))
        next_up = make_waitlist(env.db, event, "next@example.com")
        lapsed_id, next_id = lapsed.id, next_up.id
        self.run_task(env, monkeypatch, lapsed_id)
        env.db.expire_all()
        assert env.db.get(EventWaitlist, next_id).status == "promoted"
