from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.schemas.event_schema import EventCreate, EventUpdate, EventTicketType
from app.services.event_option_pricing_service import resolve_priced_selections
from app.services.event_service import _validate_complete_currency_configuration
from app.utils.event_currency import EventCurrencyConfigError, normalize_child_currencies
from app.utils.event_meals import stored_options
from app.utils.event_accommodation import stored_accommodation_options


@pytest.mark.parametrize(
    ("kind", "options"),
    [
        ("Ticket", [{"name": "General", "currency": "usd"}]),
        ("Meal", [{"name": "Lunch", "currency": "USD"}]),
        ("Accommodation", [{"name": "Shared room", "currency": "USD"}]),
    ],
)
def test_matching_child_currency_is_accepted_and_normalized(kind, options):
    assert normalize_child_currencies(options, "USD", kind)[0]["currency"] == "USD"


@pytest.mark.parametrize("kind", ["Ticket", "Meal", "Accommodation"])
def test_explicit_mismatched_child_currency_is_rejected(kind):
    with pytest.raises(EventCurrencyConfigError, match=rf"{kind} currency must match the Event currency \(USD\)\."):
        normalize_child_currencies([{"currency": "INR"}], "USD", kind)


def test_missing_ticket_currency_remains_an_event_currency_inheritance():
    assert EventTicketType(name="General", price="12").currency is None
    assert normalize_child_currencies([{"name": "General"}], "USD", "Ticket") == [{"name": "General"}]


def test_free_event_keeps_its_authoritative_currency_for_paid_add_ons():
    event = EventCreate(
        title="Free event", category="General", start_date=datetime(2030, 1, 1), end_date=datetime(2030, 1, 2),
        pricing_type="free", currency="USD",
    )

    assert event.currency == "USD"
    assert event.ticket_types == []


def test_event_currency_change_validates_existing_complete_configuration_before_write():
    event = SimpleNamespace(
        currency="USD",
        ticket_types=[{"id": "general", "currency": "USD"}],
        meals={"options": [{"id": "lunch", "currency": "USD"}]},
        accommodation={"options": [{"id": "room", "currency": "USD"}]},
    )

    with pytest.raises(EventCurrencyConfigError, match=r"Ticket currency must match the Event currency \(INR\)"):
        _validate_complete_currency_configuration(event, EventUpdate(currency="INR"), "INR")


def test_missing_service_currency_resolves_to_event_currency_on_legacy_reads():
    event = SimpleNamespace(
        currency="USD",
        modules={"meals": True, "accommodation": True},
        meals={"options": [{"id": "lunch", "name": "Lunch", "price": "5.00"}]},
        accommodation={"options": [{"id": "room", "name": "Room", "price": "20.00"}]},
    )

    assert stored_options(event)[0]["currency"] == "USD"
    assert stored_accommodation_options(event)[0]["currency"] == "USD"


def test_valid_single_currency_options_resolve_for_quote():
    event = SimpleNamespace(
        currency="USD",
        modules={"meals": True, "accommodation": True},
        meals={"options": [{"id": "lunch", "name": "Lunch", "price": "5.00", "currency": "USD"}]},
        accommodation={"options": [{"id": "room", "name": "Room", "price": "20.00", "currency": "USD"}]},
        time_zone="UTC",
    )

    priced = resolve_priced_selections(
        event, meal_selections=["lunch"], accommodation_selections=["room"], ticket_currency="USD"
    )

    assert priced.currency == "USD"
    assert priced.meal_subtotal == 5
    assert priced.accommodation_subtotal == 20


def test_legacy_mixed_currency_options_remain_defensively_rejected():
    event = SimpleNamespace(
        currency="USD",
        modules={"meals": True, "accommodation": True},
        meals={"options": [{"id": "lunch", "name": "Lunch", "price": "5.00", "currency": "INR"}]},
        accommodation={"options": []},
        time_zone="UTC",
    )

    with pytest.raises(HTTPException) as exc:
        resolve_priced_selections(event, meal_selections=["lunch"], accommodation_selections=None, ticket_currency="USD")

    assert exc.value.status_code == 422
