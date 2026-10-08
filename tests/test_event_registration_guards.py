"""Regression coverage for event lifecycle, service-window, and currency guards."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from event_sql_support import API, EventOrder, client_for, customer_user, make_enterprise, make_event, make_session, silence_side_effects, staff_user


ALL_ON = {
    "registration": True,
    "tickets": True,
    "sessions": False,
    "check_in": True,
    "online_meeting": False,
    "custom_questions": False,
    "meals": True,
    "accommodation": True,
}


@pytest.fixture
def env(monkeypatch):
    silence_side_effects(monkeypatch)
    db = make_session()
    tenant = uuid4()
    enterprise = make_enterprise(db, tenant)
    yield SimpleNamespace(db=db, tenant=tenant, enterprise=enterprise, owner=staff_user(tenant, "admin"))
    db.close()


def test_past_event_rejects_every_new_registration_path(env):
    event = make_event(
        env.db,
        env.tenant,
        env.enterprise,
        status="published",
        modules=ALL_ON,
        pricing_type="paid",
        start_date=datetime.utcnow() - timedelta(hours=3),
        end_date=datetime.utcnow() - timedelta(hours=1),
        ticket_types=[{"id": "general", "name": "General", "price": "100", "currency": "INR"}],
    )
    customer = client_for(env.db, customer_user("customer@example.com"))

    registration = customer.post(
        f"{API}/{event.id}/registrations",
        json={"participant_name": "Customer", "participant_email": "customer@example.com"},
    )
    quote = customer.post(f"{API}/{event.id}/checkout/quote", json={"ticket_type_id": "general"})
    checkout = customer.post(
        f"{API}/{event.id}/checkout",
        json={"participant_name": "Customer", "participant_email": "customer@example.com", "ticket_type_id": "general"},
    )
    waitlist = customer.post(
        f"{API}/{event.id}/waitlist",
        json={"participant_name": "Customer", "participant_email": "customer@example.com"},
    )
    walk_in = client_for(env.db, env.owner).post(
        f"{API}/{event.id}/walk-in",
        json={"participant_name": "Staff customer", "participant_email": "walkin@example.com", "ticket_type_id": "general"},
    )

    for response in (registration, quote, checkout, waitlist, walk_in):
        assert response.status_code == 400
        assert "event has already ended" in response.json()["detail"].lower() or "event has finished" in response.json()["detail"].lower()


@pytest.mark.parametrize(
    ("field", "selection", "expected"),
    [
        ("meals", "meal_selections", "Meal service period has ended"),
        ("accommodation", "accommodation_selections", "Accommodation period has ended"),
    ],
)
def test_quote_rejects_options_after_their_service_period(env, field, selection, expected):
    option_id = "lunch" if field == "meals" else "room"
    option = {
        "id": option_id,
        "name": "Lunch" if field == "meals" else "Room",
        "price": "50",
        "currency": "INR",
        "service_start_at": (datetime.utcnow() - timedelta(days=2)).isoformat(),
        "service_end_at": (datetime.utcnow() - timedelta(days=1)).isoformat(),
    }
    event = make_event(
        env.db,
        env.tenant,
        env.enterprise,
        status="published",
        modules=ALL_ON,
        pricing_type="paid",
        ticket_types=[{"id": "general", "name": "General", "price": "100", "currency": "INR"}],
        **{field: {"options": [option]}},
    )

    response = client_for(env.db, customer_user("customer@example.com")).post(
        f"{API}/{event.id}/checkout/quote",
        json={"ticket_type_id": "general", selection: [option_id]},
    )
    assert response.status_code == 422
    assert expected in response.json()["detail"]


def test_free_ticket_uses_paid_add_on_currency_and_rejects_mixed_paid_options(env):
    event = make_event(
        env.db,
        env.tenant,
        env.enterprise,
        status="published",
        modules=ALL_ON,
        pricing_type="free",
        currency="INR",
        meals={"options": [{"id": "usd-meal", "name": "USD Meal", "price": "10", "currency": "USD"}]},
        accommodation={"options": [{"id": "inr-room", "name": "INR Room", "price": "500", "currency": "INR"}]},
    )
    customer = client_for(env.db, customer_user("customer@example.com"))

    allowed = customer.post(
        f"{API}/{event.id}/registrations",
        json={"participant_name": "Customer", "participant_email": "customer@example.com", "meal_selections": ["usd-meal"]},
    )
    assert allowed.status_code == 201, allowed.text
    (order,) = env.db.query(EventOrder).all()
    assert (order.amount, order.currency) == ("10.00", "USD")

    rejected = client_for(env.db, customer_user("another@example.com")).post(
        f"{API}/{event.id}/registrations",
        json={
            "participant_name": "Another",
            "participant_email": "another@example.com",
            "meal_selections": ["usd-meal"],
            "accommodation_selections": ["inr-room"],
        },
    )
    assert rejected.status_code == 422
    assert "different currencies" in rejected.json()["detail"]
