"""Default ticket: the Event's Base Price as an independently purchasable option.

An Event priced through ``Event.price`` can sell that Base Price through the ordinary ticket flow with a real,
backend-generated id kept in ``Event.default_ticket_type_id`` -- whether or not it ALSO has configured Ticket Types
(Regular, VIP, ...). The two are independent options: the default ticket is rebuilt from the current ``price`` and
``currency`` on every use (so a price change reaches new quotes while historical orders keep their stored amounts),
configured tickets keep their own ids and prices, and the default is never written into ``ticket_types``.

Eligibility (all three, re-checked on every use): a positive Base Price, a valid Event currency, and Base Price
enabled in the Event's pinned Event Form version. The id is assigned once and never changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.utils.event_currency import EventCurrencyConfigError, normalize_event_currency
from app.utils.event_payments import parse_money

DEFAULT_TICKET_NAME = "General admission"


def has_configured_ticket_types(ticket_types: Any) -> bool:
    return any(isinstance(t, dict) for t in (ticket_types or []))


def base_price_is_purchasable(price: Any, currency: Any, ticket_types: Any = None) -> bool:
    """A positive Base Price and a valid Event currency.

    ``ticket_types`` is accepted only for call-site compatibility: configured Ticket Types used to make the Base
    Price ineligible, which is exactly what hid the default ticket on Events that also sell a VIP ticket. They are
    independent options now and play no part here. (Form enablement needs the database, so callers check it
    separately: ``core_field_enabled(db, version_id, "price")``.)
    """
    amount = parse_money(price)
    if amount is None or amount <= 0:
        return False
    try:
        return normalize_event_currency(currency, required=True) is not None
    except EventCurrencyConfigError:
        return False


def event_default_ticket_is_purchasable(event) -> bool:
    return bool(getattr(event, "default_ticket_type_id", None)) and base_price_is_purchasable(
        getattr(event, "price", None), getattr(event, "currency", None)
    )


def purchasable_default_ticket_id(event) -> str | None:
    """The stored id, but only while the Event can actually sell it (otherwise clients must not offer it)."""
    return str(event.default_ticket_type_id) if event_default_ticket_is_purchasable(event) else None


def _default_ticket(event) -> dict:
    """Capacity is deliberately absent: the default ticket has no quota of its own, so only the Event-level
    capacity (enforced by every purchase flow under the Event row lock) limits it."""
    return {
        "id": str(event.default_ticket_type_id),
        "name": DEFAULT_TICKET_NAME,
        "price": event.price,
        "currency": event.currency,
        "is_default": True,
    }


def default_ticket_for(event, ticket_type_id: Any) -> dict | None:
    """The default ticket when ``ticket_type_id`` is this Event's purchasable default id, else None."""
    if not ticket_type_id or not event_default_ticket_is_purchasable(event):
        return None
    if str(ticket_type_id).strip().lower() != str(event.default_ticket_type_id).lower():
        return None
    return _default_ticket(event)


@dataclass(frozen=True)
class TicketResolution:
    """What a requested ``ticket_type_id`` means for an Event.

    kind:
      * ``configured``  -- the id of a configured Ticket Type (Regular, VIP, ...); ``ticket`` is that entry.
      * ``default``     -- this Event's Base Price default ticket and it is currently sellable.
      * ``flat``        -- no id was sent: the legacy single-price path (unchanged).
      * ``ineligible``  -- the id IS this Event's stored default id, but Base Price can't be sold right now
                           (no positive price, invalid currency, or Base Price disabled in the pinned form).
      * ``invalid``     -- unknown or stale id (not configured, not this Event's default id).
    """

    kind: str
    ticket: dict | None = None


def resolve_ticket_option(
    event, ticket_type_id: Any, base_price_enabled: Callable[[], bool] = lambda: True
) -> TicketResolution:
    """The single place that maps a ticket id to a sellable ticket; quote, checkout and walk-in all use it.

    A configured Ticket Type always wins, so an id can never be reinterpreted as the default ticket.
    ``base_price_enabled`` is called lazily (only for a default-id match) so form lookups cost nothing otherwise.
    """
    if not ticket_type_id:
        return TicketResolution("flat", {"price": event.price or "0", "currency": event.currency or "INR", "capacity": event.capacity})

    for ticket in getattr(event, "ticket_types", None) or []:
        if isinstance(ticket, dict) and str(ticket.get("id")) == str(ticket_type_id):
            return TicketResolution("configured", ticket)

    stored = getattr(event, "default_ticket_type_id", None)
    if not stored or str(ticket_type_id).strip().lower() != str(stored).lower():
        return TicketResolution("invalid")
    if not event_default_ticket_is_purchasable(event) or not base_price_enabled():
        return TicketResolution("ineligible")
    return TicketResolution("default", _default_ticket(event))
