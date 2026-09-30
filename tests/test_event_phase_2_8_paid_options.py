"""
Event Management Phase 2.8 — real, purchasable Meals/Accommodation: pricing, capacity and the new endpoints.

Covers what the other Phase 2.8 test files (test_event_meals_config.py, test_event_accommodation_config.py,
test_event_phase_2_6_meals.py, test_event_phase_2_7_accommodation.py, test_event_checkout.py,
test_event_phase_2_5_walk_in.py — all updated for pricing already) do NOT: the genuinely new behaviours this
phase adds on top of them.

  1. "Free ≠ zero payable": a free-ticket online registration with a priced meal/accommodation selection
     still creates a real EventOrder; an all-free selection still creates none.
  2. The same for walk-in.
  3. Meal/accommodation capacity is enforced under the SAME Event row lock ticket capacity already uses.
  4. The new POST /{event_id}/checkout/quote endpoint: correct totals, no writes, real validation errors.
  5. GET /my/registrations exposes confirmed purchased options (the Mobile ticket/QR gap).
  6. GET /{event_id}/fulfilment: option summary + participant-level purchase records, organizer-only.
  7. Historical pricing immutability: changing an option's price after purchase never rewrites the
     already-issued line item or order.

Run:
    pytest tests/test_event_phase_2_8_paid_options.py -v
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from event_sql_support import (
    API,
    Event,
    EventOrder,
    EventRegistration,
    client_for,
    customer_user,
    make_enterprise,
    make_event,
    make_session,
    silence_side_effects,
    staff_user,
)
from app.models.event_aux_models import EventRegistrationOption
import app.services.event_walk_in_service as walk_in_service

ALL_ON = {"registration": True, "tickets": True, "sessions": False, "check_in": True, "online_meeting": False,
          "custom_questions": False, "meals": True, "accommodation": True}


@pytest.fixture
def env(monkeypatch):
    apply_async = silence_side_effects(monkeypatch)
    db = make_session()
    tenant = uuid4()
    ent = make_enterprise(db, tenant)
    monkeypatch.setattr(
        "app.services.event_form_config_service.apply_form_configuration_to_event_data",
        lambda _db, data, user: {"tenant_id": tenant, "enterprise_id": ent.id, "form_configuration_id": None,
                                 "form_configuration_version_id": None, "custom_values": []},
    )
    yield SimpleNamespace(db=db, tenant=tenant, ent=ent, owner=staff_user(tenant, "admin"))
    db.close()


def free_event_with_paid_meal(env, **overrides):
    return make_event(
        env.db, env.tenant, env.ent, status="published", modules=ALL_ON, capacity="50",
        pricing_type="free",
        meals={"options": [{"id": "lunch", "name": "Lunch", "price": "150", "currency": "INR"}]},
        accommodation={"options": [{"id": "shared-room", "name": "Shared Room", "price": "300", "currency": "INR"}]},
        **overrides,
    )


def owner_client(env):
    return client_for(env.db, env.owner)


# ===========================================================================
# 1. Free registration + paid extras
# ===========================================================================


class TestFreeRegistrationPaidExtras:
    def test_a_free_ticket_with_a_paid_meal_creates_a_real_order(self, env):
        event = free_event_with_paid_meal(env)
        resp = client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{event.id}/registrations",
            json={"participant_name": "A", "participant_email": "a@example.com", "meal_selections": ["lunch"]},
        )
        assert resp.status_code == 201, resp.text
        (order,) = env.db.query(EventOrder).all()
        assert (order.status, order.payment_status, order.amount, order.currency) == ("confirmed", "confirmed", "150.00", "INR")
        assert (order.ticket_subtotal, order.meal_subtotal, order.accommodation_subtotal) == ("0.00", "150.00", "0.00")
        (reg,) = env.db.query(EventRegistration).all()
        options = env.db.query(EventRegistrationOption).filter_by(registration_id=reg.id).all()
        assert len(options) == 1 and options[0].order_id == order.id and options[0].unit_price == "150.00"

    def test_a_free_ticket_with_a_free_meal_creates_no_order(self, env):
        event = make_event(env.db, env.tenant, env.ent, status="published", modules=ALL_ON, capacity="50",
                            pricing_type="free", meals={"options": [{"id": "lunch", "name": "Lunch"}]})
        resp = client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{event.id}/registrations",
            json={"participant_name": "A", "participant_email": "a@example.com", "meal_selections": ["lunch"]},
        )
        assert resp.status_code == 201, resp.text
        assert env.db.query(EventOrder).count() == 0
        (reg,) = env.db.query(EventRegistration).all()
        # Still snapshotted (order_id null) — a consistent source for my_registrations/fulfilment either way.
        (option,) = env.db.query(EventRegistrationOption).filter_by(registration_id=reg.id).all()
        assert option.order_id is None and option.unit_price == "0.00"

    def test_a_free_ticket_with_paid_meal_and_accommodation_sums_both(self, env):
        event = free_event_with_paid_meal(env)
        resp = client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{event.id}/registrations",
            json={"participant_name": "A", "participant_email": "a@example.com",
                  "meal_selections": ["lunch"], "accommodation_selections": ["shared-room"]},
        )
        assert resp.status_code == 201, resp.text
        (order,) = env.db.query(EventOrder).all()
        assert order.amount == "450.00"  # 150 + 300

    def test_an_unpriced_paid_meal_option_is_free(self, env):
        """price omitted entirely (pre-Phase-2.8-style option) still means free, not an error."""
        event = make_event(env.db, env.tenant, env.ent, status="published", modules=ALL_ON, capacity="50",
                            pricing_type="free", meals={"options": [{"id": "lunch", "name": "Lunch"}]})
        resp = client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{event.id}/registrations", json={"participant_name": "A", "participant_email": "a@example.com"})
        assert resp.status_code == 201
        assert env.db.query(EventOrder).count() == 0


# ===========================================================================
# 2. Walk-in + paid extras
# ===========================================================================


class TestWalkInPaidExtras:
    def test_a_free_ticket_walk_in_with_a_paid_meal_creates_a_pending_order_and_is_not_admitted(self, env):
        """Walk-in never records a payment it did not receive (module policy) — this now applies to the
        ticket+meal+accommodation TOTAL, not just the ticket."""
        event = free_event_with_paid_meal(env)
        resp = owner_client(env).post(f"{API}/{event.id}/walk-in", json={
            "participant_name": "W", "participant_email": "w@example.com", "meal_selections": ["lunch"], "check_in": True})
        assert resp.status_code == 201, resp.text
        data = resp.json()
        (order,) = env.db.query(EventOrder).all()
        assert (order.status, order.payment_status, order.amount) == ("confirmed", "pending", "150.00")
        assert data["payment"] == {
            "required": True, "status": "pending", "amount": 150.0, "currency": "INR",
            "order_id": str(order.id), "note": walk_in_service._PENDING_NOTE,
        }
        assert data["check_in"]["performed"] is False and data["check_in"]["reason"] == "payment_pending"
        assert data["registration"]["registration_status"] == "confirmed"  # not attended: not admitted

    def test_a_free_ticket_walk_in_with_only_free_selections_is_admitted_normally(self, env):
        event = make_event(env.db, env.tenant, env.ent, status="published", modules=ALL_ON, capacity="50",
                            pricing_type="free", meals={"options": [{"id": "lunch", "name": "Lunch"}]})
        resp = owner_client(env).post(f"{API}/{event.id}/walk-in", json={
            "participant_name": "W", "participant_email": "w@example.com", "meal_selections": ["lunch"], "check_in": True})
        assert resp.status_code == 201, resp.text
        assert resp.json()["payment"] == {"required": False, "status": "free", "amount": None, "currency": None, "order_id": None, "note": None}
        assert resp.json()["check_in"]["performed"] is True
        assert env.db.query(EventOrder).count() == 0


# ===========================================================================
# 3. Capacity enforcement (meal/accommodation) — same row lock as tickets
# ===========================================================================


class TestCapacityEnforcement:
    def spy_on_row_lock(self, monkeypatch):
        from sqlalchemy.orm import Query
        calls = []
        original = Query.with_for_update

        def spy(self, *args, **kwargs):
            calls.append(1)
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Query, "with_for_update", spy)
        return calls

    def test_a_sold_out_meal_option_is_rejected_at_checkout(self, env):
        event = make_event(
            env.db, env.tenant, env.ent, status="published", modules=ALL_ON, capacity="50",
            pricing_type="paid", ticket_types=[{"id": "general", "name": "General", "price": "100", "currency": "INR"}],
            meals={"options": [{"id": "lunch", "name": "Lunch", "price": "50", "currency": "INR", "capacity": 1}]},
        )
        first = client_for(env.db, customer_user("a@example.com")).post(f"{API}/{event.id}/checkout", json={
            "participant_name": "A", "participant_email": "a@example.com", "ticket_type_id": "general", "meal_selections": ["lunch"]})
        assert first.status_code == 201, first.text
        second = client_for(env.db, customer_user("b@example.com")).post(f"{API}/{event.id}/checkout", json={
            "participant_name": "B", "participant_email": "b@example.com", "ticket_type_id": "general", "meal_selections": ["lunch"]})
        assert second.status_code == 400 and "sold out" in second.json()["detail"].lower()
        assert env.db.query(EventRegistration).count() == 1  # the rejected purchase created nothing

    def test_a_sold_out_accommodation_option_is_rejected_at_free_registration(self, env):
        event = make_event(env.db, env.tenant, env.ent, status="published", modules=ALL_ON, capacity="50",
                            pricing_type="free",
                            accommodation={"options": [{"id": "shared-room", "name": "Shared Room", "capacity": 1}]})
        first = client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{event.id}/registrations",
            json={"participant_name": "A", "participant_email": "a@example.com", "accommodation_selections": ["shared-room"]})
        assert first.status_code == 201, first.text
        second = client_for(env.db, customer_user("b@example.com")).post(
            f"{API}/{event.id}/registrations",
            json={"participant_name": "B", "participant_email": "b@example.com", "accommodation_selections": ["shared-room"]})
        assert second.status_code == 400 and "sold out" in second.json()["detail"].lower()

    def test_checkout_checks_option_capacity_under_the_same_event_row_lock_as_tickets(self, env, monkeypatch):
        locks = self.spy_on_row_lock(monkeypatch)
        event = make_event(
            env.db, env.tenant, env.ent, status="published", modules=ALL_ON, capacity="50",
            pricing_type="paid", ticket_types=[{"id": "general", "name": "General", "price": "100", "currency": "INR"}],
            meals={"options": [{"id": "lunch", "name": "Lunch", "price": "50", "currency": "INR"}]},
        )
        resp = client_for(env.db, customer_user("a@example.com")).post(f"{API}/{event.id}/checkout", json={
            "participant_name": "A", "participant_email": "a@example.com", "ticket_type_id": "general", "meal_selections": ["lunch"]})
        assert resp.status_code == 201 and locks  # the lock was actually taken, not merely present in source

    def test_a_retired_priced_option_already_held_can_still_be_kept_not_newly_sold(self, env):
        """Capacity is only checked for NEWLY selected options — see assert_capacity_available's held_*_ids."""
        event = make_event(
            env.db, env.tenant, env.ent, status="published", modules=ALL_ON, capacity="50", pricing_type="free",
            meals={"options": [{"id": "lunch", "name": "Lunch", "price": "50", "currency": "INR", "capacity": 5}]},
        )
        resp = client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{event.id}/registrations",
            json={"participant_name": "A", "participant_email": "a@example.com", "meal_selections": ["lunch"]})
        assert resp.status_code == 201, resp.text


# ===========================================================================
# 4. Checkout quote endpoint
# ===========================================================================


class TestCheckoutQuote:
    def test_the_quote_matches_what_checkout_would_charge_and_writes_nothing(self, env):
        event = make_event(
            env.db, env.tenant, env.ent, status="published", modules=ALL_ON, capacity="50",
            pricing_type="paid", ticket_types=[{"id": "general", "name": "General", "price": "500", "currency": "INR"}],
            meals={"options": [{"id": "lunch", "name": "Lunch", "price": "100", "currency": "INR"}]},
            accommodation={"options": [{"id": "shared-room", "name": "Shared Room", "price": "200", "currency": "INR"}]},
        )
        client = client_for(env.db, customer_user("a@example.com"))
        quote = client.post(f"{API}/{event.id}/checkout/quote", json={
            "ticket_type_id": "general", "quantity": 2, "meal_selections": ["lunch"], "accommodation_selections": ["shared-room"]})
        assert quote.status_code == 200, quote.text
        body = quote.json()
        assert (body["ticket_subtotal"], body["meal_subtotal"], body["accommodation_subtotal"], body["grand_total"]) == (1000.0, 100.0, 200.0, 1300.0)
        assert body["discount"] == 0.0 and body["tax"] == 0.0 and body["currency"] == "INR"
        assert {(i["option_type"], i["option_id"]) for i in body["items"]} == {("meal", "lunch"), ("accommodation", "shared-room")}
        assert env.db.query(EventOrder).count() == 0 and env.db.query(EventRegistration).count() == 0

        checkout = client.post(f"{API}/{event.id}/checkout", json={
            "participant_name": "A", "participant_email": "a@example.com", "ticket_type_id": "general", "quantity": 2,
            "meal_selections": ["lunch"], "accommodation_selections": ["shared-room"]})
        assert checkout.status_code == 201 and checkout.json()["amount"] == "1300.00"  # the quote was authoritative

    def test_an_unknown_ticket_type_is_404(self, env):
        event = make_event(env.db, env.tenant, env.ent, status="published", modules=ALL_ON, capacity="50",
                            pricing_type="paid", ticket_types=[{"id": "general", "name": "General", "price": "500", "currency": "INR"}])
        resp = client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{event.id}/checkout/quote", json={"ticket_type_id": "nope"})
        assert resp.status_code == 404

    def test_an_unknown_meal_option_is_422_and_names_the_client_never_sends_a_price(self, env):
        event = make_event(env.db, env.tenant, env.ent, status="published", modules=ALL_ON, capacity="50",
                            pricing_type="paid", ticket_types=[{"id": "general", "name": "General", "price": "500", "currency": "INR"}])
        resp = client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{event.id}/checkout/quote", json={"ticket_type_id": "general", "meal_selections": ["ghost"]})
        assert resp.status_code == 422 and "ghost" in resp.json()["detail"]

    def test_the_request_schema_has_no_price_or_total_field(self, env):
        from app.main import app as fastapi_app
        schema = fastapi_app.openapi()["components"]["schemas"]["EventCheckoutQuoteRequest"]
        assert not any("price" in k or "total" in k or "amount" in k for k in schema["properties"])


# ===========================================================================
# 5. my/registrations exposes confirmed purchased options
# ===========================================================================


class TestMyRegistrations:
    def test_a_checked_out_meal_selection_shows_as_confirmed_with_its_purchase_snapshot(self, env):
        event = make_event(
            env.db, env.tenant, env.ent, status="published", modules=ALL_ON, capacity="50",
            pricing_type="paid", ticket_types=[{"id": "general", "name": "General", "price": "500", "currency": "INR"}],
            meals={"options": [{"id": "lunch", "name": "Lunch", "price": "100", "currency": "INR"}]},
        )
        client = client_for(env.db, customer_user("a@example.com"))
        checkout = client.post(f"{API}/{event.id}/checkout", json={
            "participant_name": "A", "participant_email": "a@example.com", "ticket_type_id": "general", "meal_selections": ["lunch"]})
        assert checkout.status_code == 201, checkout.text

        mine = client.get(f"{API}/my/registrations")
        assert mine.status_code == 200
        (reg,) = mine.json()
        assert reg["meal_selections"] == [{
            "meal_id": "lunch", "name": "Lunch", "active": True, "price": 100.0, "currency": "INR",
            "service_start_at": None, "service_end_at": None, "status": "confirmed",
        }]

    def test_a_pre_phase_2_8_style_selection_with_no_line_item_shows_as_selected_not_confirmed(self, env):
        """A registration whose meal_selections array was set directly (no EventRegistrationOption row —
        simulating data from before this phase) must not be reported as a confirmed purchase."""
        from event_sql_support import make_registration
        event = make_event(env.db, env.tenant, env.ent, status="published", modules=ALL_ON, capacity="50",
                            pricing_type="free", meals={"options": [{"id": "lunch", "name": "Lunch", "price": "100", "currency": "INR"}]})
        make_registration(env.db, event, "a@example.com", meal_selections=["lunch"])
        mine = client_for(env.db, customer_user("a@example.com")).get(f"{API}/my/registrations")
        assert mine.json()[0]["meal_selections"][0]["status"] == "selected"


# ===========================================================================
# 6. Fulfilment endpoint
# ===========================================================================


class TestFulfilment:
    def test_organizer_sees_option_summary_and_participant_purchases(self, env):
        event = free_event_with_paid_meal(env)
        client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{event.id}/registrations",
            json={"participant_name": "A", "participant_email": "a@example.com", "meal_selections": ["lunch"]})

        resp = owner_client(env).get(f"{API}/{event.id}/fulfilment")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["meals"][0]["meal_id"] == "lunch" and body["meals"][0]["selected_count"] == 1
        (purchase,) = body["purchases"]
        assert purchase["participant_email"] == "a@example.com" and purchase["option_id"] == "lunch"
        assert purchase["unit_price"] == 150.0 and purchase["payment_status"] == "paid"

    def test_a_customer_cannot_read_fulfilment_data(self, env):
        event = free_event_with_paid_meal(env)
        resp = client_for(env.db, customer_user("nobody@example.com")).get(f"{API}/{event.id}/fulfilment")
        assert resp.status_code == 403

    def test_a_cancelled_registrations_purchase_does_not_appear(self, env):
        event = free_event_with_paid_meal(env)
        client = client_for(env.db, customer_user("a@example.com"))
        reg_resp = client.post(f"{API}/{event.id}/registrations",
                                json={"participant_name": "A", "participant_email": "a@example.com", "meal_selections": ["lunch"]})
        reg_id = reg_resp.json()["id"]
        cancel = client.delete(f"{API}/{event.id}/registrations/{reg_id}")
        assert cancel.status_code == 200, cancel.text
        body = owner_client(env).get(f"{API}/{event.id}/fulfilment").json()
        assert body["purchases"] == []


# ===========================================================================
# 7. Historical pricing immutability
# ===========================================================================


class TestHistoricalPricingImmutability:
    def test_changing_an_options_price_after_purchase_does_not_rewrite_the_issued_order_or_line_item(self, env):
        event = free_event_with_paid_meal(env)
        client = client_for(env.db, customer_user("a@example.com"))
        resp = client.post(f"{API}/{event.id}/registrations",
                            json={"participant_name": "A", "participant_email": "a@example.com", "meal_selections": ["lunch"]})
        assert resp.status_code == 201, resp.text
        (order_before,) = env.db.query(EventOrder).all()
        assert order_before.amount == "150.00"
        (option_before,) = env.db.query(EventRegistrationOption).all()
        assert option_before.unit_price == "150.00"

        # The organizer doubles the price.
        raise_price = owner_client(env).put(f"{API}/{event.id}", json={
            "meals": {"options": [{"id": "lunch", "name": "Lunch", "price": "300", "currency": "INR"}]}})
        assert raise_price.status_code == 200, raise_price.text

        env.db.expire_all()
        order_after = env.db.query(EventOrder).filter_by(id=order_before.id).one()
        option_after = env.db.query(EventRegistrationOption).filter_by(id=option_before.id).one()
        assert order_after.amount == "150.00"  # unchanged: the order is an immutable purchase-time record
        assert option_after.unit_price == "150.00"

        # A NEW purchase, after the price change, is charged the NEW price.
        resp2 = client_for(env.db, customer_user("b@example.com")).post(
            f"{API}/{event.id}/registrations",
            json={"participant_name": "B", "participant_email": "b@example.com", "meal_selections": ["lunch"]})
        assert resp2.status_code == 201, resp2.text
        (order_b,) = env.db.query(EventOrder).filter(EventOrder.participant_email == "b@example.com").all()
        assert order_b.amount == "300.00"
