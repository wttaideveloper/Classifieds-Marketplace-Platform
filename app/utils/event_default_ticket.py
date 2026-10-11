"""Default ticket for Base-Price-only Events.

An Event that is priced only through ``Event.price`` (no configured Ticket Types) can be bought through the
ordinary ticket flow with a real, backend-generated id kept in ``Event.default_ticket_type_id``. The id is
assigned once and never changes; the TICKET it resolves to is rebuilt from the current ``price`` and
``currency`` on every use, so a price change reaches new quotes while historical orders keep their stored
amounts. It is never written into ``ticket_types``.
"""

from __future__ import annotations

from typing import Any

from app.utils.event_currency import EventCurrencyConfigError, normalize_event_currency
from app.utils.event_payments import parse_money

DEFAULT_TICKET_NAME = "General admission"


def has_configured_ticket_types(ticket_types: Any) -> bool:
    return any(isinstance(t, dict) for t in (ticket_types or []))


def base_price_is_purchasable(price: Any, currency: Any, ticket_types: Any) -> bool:
    """A positive Base Price, a valid Event currency and no configured Ticket Types."""
    amount = parse_money(price)
    if amount is None or amount <= 0:
        return False
    try:
        if normalize_event_currency(currency, required=True) is None:
            return False
    except EventCurrencyConfigError:
        return False
    return not has_configured_ticket_types(ticket_types)


def event_default_ticket_is_purchasable(event) -> bool:
    return bool(getattr(event, "default_ticket_type_id", None)) and base_price_is_purchasable(
        getattr(event, "price", None), getattr(event, "currency", None), getattr(event, "ticket_types", None)
    )


def purchasable_default_ticket_id(event) -> str | None:
    """The stored id, but only while the Event can actually sell it (otherwise clients must not offer it)."""
    return str(event.default_ticket_type_id) if event_default_ticket_is_purchasable(event) else None


def default_ticket_for(event, ticket_type_id: Any) -> dict | None:
    """The default ticket when ``ticket_type_id`` is this Event's purchasable default id, else None.

    Capacity is deliberately absent: with no other ticket types the Event-level capacity (enforced by every
    purchase flow under the Event row lock) is the only limit.
    """
    if not ticket_type_id or not event_default_ticket_is_purchasable(event):
        return None
    if str(ticket_type_id).strip().lower() != str(event.default_ticket_type_id).lower():
        return None
    return {
        "id": str(event.default_ticket_type_id),
        "name": DEFAULT_TICKET_NAME,
        "price": event.price,
        "currency": event.currency,
        "is_default": True,
    }
