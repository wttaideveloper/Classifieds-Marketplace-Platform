"""Pricing, capacity enforcement, and line-item creation for paid Meal/Accommodation options (Phase 2.8).

ONE authoritative implementation, reused by online registration, checkout, and walk-in (Section 22 of the spec:
"Do not duplicate business rules across registration/checkout/walk-in/admin"). Nothing here computes a client-
trusted price or total — every price is re-resolved from the event's own stored configuration.

Design decisions (documented here since they're load-bearing for every caller):

1. No separate "reserved" capacity state. This app's checkout has always been fully synchronous — there is no
   payment webhook or gateway anywhere in the codebase (verified), so there is never a window where a purchase
   is "in flight" across requests. Capacity is therefore checked-and-consumed atomically in ONE step, under the
   same `SELECT ... FOR UPDATE` row lock on the Event that ticket-capacity checks already take
   (event_service.py's create_event_checkout_service). This is the "safest compatible approach" the spec asks
   for when a true reservation phase isn't supported by the existing architecture.

2. No separate capacity counter to increment/decrement. "Remaining capacity" is COUNTED live from
   EventRegistrationOption rows joined to their registration's current status (confirmed/attended). A
   registration (or its paired order) moving to cancelled/refunded already flips EventRegistration.status via
   existing code (_cancel_registration_and_release_seat, update_event_order_status_service) — so capacity is
   released automatically, exactly once, by construction: there is no counter to double-decrement, and nothing
   here needs to run again on refund/cancellation for capacity purposes.

3. Currency: every selected option's currency (defaulting to the event's currency when the option doesn't set
   its own) must match the ticket currency. Mixed currencies in one order are rejected (422), never silently
   summed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.event_aux_models import EventRegistration, EventRegistrationOption
from app.utils.event_accommodation import (
    AccommodationSelectionError,
    option_price as accommodation_option_price,
    stored_accommodation_options,
    validate_accommodation_selections,
)
from app.utils.event_meals import (
    MealSelectionError,
    option_price as meal_option_price,
    stored_options as stored_meal_options,
    validate_meal_selections,
)
from app.utils.event_payments import money_to_float

# Quantized (not Decimal("0")): an empty sum() must land on the same 2dp scale a non-empty one gets from its
# already-quantized PricedLine.unit_price values, or meal_subtotal/accommodation_subtotal would inconsistently
# format as "0" (empty) vs "150.00" (non-empty) — the same trap option_price() had (see event_meals.option_price).
ZERO = Decimal("0.00")
_ACTIVE_REGISTRATION_STATUSES = ("confirmed", "attended")


@dataclass
class PricedLine:
    option_type: str  # "meal" | "accommodation"
    option_id: str
    name: str
    unit_price: Decimal
    currency: str
    quantity: int = 1

    @property
    def line_total(self) -> Decimal:
        return self.unit_price * self.quantity


@dataclass
class PricedSelections:
    meal_lines: list[PricedLine] = field(default_factory=list)
    accommodation_lines: list[PricedLine] = field(default_factory=list)
    currency: str | None = None  # None only when nothing was priced and no ticket currency was supplied

    @property
    def meal_subtotal(self) -> Decimal:
        return sum((line.line_total for line in self.meal_lines), ZERO)

    @property
    def accommodation_subtotal(self) -> Decimal:
        return sum((line.line_total for line in self.accommodation_lines), ZERO)

    @property
    def all_lines(self) -> list[PricedLine]:
        return [*self.meal_lines, *self.accommodation_lines]


def _option_currency(option: dict, event_currency: str) -> str:
    currency = option.get("currency")
    return currency if isinstance(currency, str) and currency.strip() else (event_currency or "INR")


def resolve_priced_selections(
    event,
    *,
    meal_selections: list[str] | None,
    accommodation_selections: list[str] | None,
    current_meal_selections: list[str] | None = None,
    current_accommodation_selections: list[str] | None = None,
    ticket_currency: str | None = None,
) -> PricedSelections:
    """Validate meal/accommodation selections against the event's configuration (ids exist, belong to this
    event, are active-or-already-held, are inside their purchase window — all via the existing pure validators)
    and resolve each to a snapshot price/currency. Raises HTTPException(422) for any contract violation,
    INCLUDING mixed currencies. Does not touch the database — no capacity check, no writes (see
    assert_capacity_available / persist_line_items below for that).
    """
    event_currency = getattr(event, "currency", None) or "INR"

    try:
        validated_meals = validate_meal_selections(event, meal_selections, current=current_meal_selections)
    except MealSelectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        validated_accommodation = validate_accommodation_selections(
            event, accommodation_selections, current=current_accommodation_selections
        )
    except AccommodationSelectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    meal_options = {option["id"]: option for option in stored_meal_options(event)}
    accommodation_options = {option["id"]: option for option in stored_accommodation_options(event)}

    meal_lines = [
        PricedLine(
            option_type="meal",
            option_id=option_id,
            name=meal_options[option_id]["name"],
            unit_price=meal_option_price(meal_options[option_id]),
            currency=_option_currency(meal_options[option_id], event_currency),
        )
        for option_id in (validated_meals or [])
    ]
    accommodation_lines = [
        PricedLine(
            option_type="accommodation",
            option_id=option_id,
            name=accommodation_options[option_id]["name"],
            unit_price=accommodation_option_price(accommodation_options[option_id]),
            currency=_option_currency(accommodation_options[option_id], event_currency),
        )
        for option_id in (validated_accommodation or [])
    ]

    currencies = {line.currency for line in (*meal_lines, *accommodation_lines)}
    if ticket_currency:
        currencies.add(ticket_currency)
    if len(currencies) > 1:
        raise HTTPException(
            status_code=422,
            detail=f"Selected options use different currencies ({', '.join(sorted(currencies))}); a single order must use one currency.",
        )
    resolved_currency = next(iter(currencies), None) or ticket_currency

    return PricedSelections(meal_lines=meal_lines, accommodation_lines=accommodation_lines, currency=resolved_currency)


def _count_active_selections(db: Session, event_id: UUID, option_type: str, option_id: str) -> int:
    """How many CONFIRMED/ATTENDED registrations currently hold this option — the live, always-correct
    "capacity consumed" figure (see module docstring, point 2). Must be called after the caller has already
    taken the Event row lock, so this count is serialized against concurrent purchases of the same option."""
    return (
        db.query(EventRegistrationOption)
        .join(EventRegistration, EventRegistrationOption.registration_id == EventRegistration.id)
        .filter(
            EventRegistrationOption.event_id == event_id,
            EventRegistrationOption.option_type == option_type,
            EventRegistrationOption.option_id == option_id,
            EventRegistration.status.in_(_ACTIVE_REGISTRATION_STATUSES),
        )
        .count()
    )


def assert_capacity_available(
    db: Session,
    event,
    priced: PricedSelections,
    *,
    held_meal_ids: frozenset[str] = frozenset(),
    held_accommodation_ids: frozenset[str] = frozenset(),
) -> None:
    """Raise HTTPException(400) naming the first sold-out option. MUST be called while the caller already holds
    the Event row's `SELECT ... FOR UPDATE` lock (same lock ticket capacity uses) — this function does not take
    a lock itself, by design, so callers control the transaction boundary exactly like they already do for
    tickets. `held_*_ids` are options this SAME registration already holds (keeping a retired-but-held option
    is allowed elsewhere; here it means "don't count myself against my own capacity" — relevant when this is
    called for an update rather than a brand-new registration).
    """
    meal_options = {option["id"]: option for option in stored_meal_options(event)}
    accommodation_options = {option["id"]: option for option in stored_accommodation_options(event)}

    for line in priced.meal_lines:
        if line.option_id in held_meal_ids:
            continue
        option = meal_options.get(line.option_id, {})
        capacity = option.get("capacity")
        if capacity is None:
            continue
        taken = _count_active_selections(db, event.id, "meal", line.option_id)
        if taken >= int(capacity):
            raise HTTPException(status_code=400, detail=f"Meal option sold out: {line.name}")

    for line in priced.accommodation_lines:
        if line.option_id in held_accommodation_ids:
            continue
        option = accommodation_options.get(line.option_id, {})
        capacity = option.get("capacity")
        if capacity is None:
            continue
        taken = _count_active_selections(db, event.id, "accommodation", line.option_id)
        if taken >= int(capacity):
            raise HTTPException(status_code=400, detail=f"Accommodation option sold out: {line.name}")


def persist_line_items(
    db: Session,
    *,
    event_id: UUID,
    registration_id: UUID,
    order_id: UUID | None,
    priced: PricedSelections,
) -> list[EventRegistrationOption]:
    """Create (db.add + flush, no commit — the caller's existing transaction owns the commit) one immutable
    snapshot row per newly-purchased option. Only ever called with the NEWLY selected lines (not ones the
    registration already held), so re-selecting an already-held option never creates a duplicate row."""
    rows: list[EventRegistrationOption] = []
    for line in priced.all_lines:
        row = EventRegistrationOption(
            event_id=event_id,
            registration_id=registration_id,
            order_id=order_id,
            option_type=line.option_type,
            option_id=line.option_id,
            option_name=line.name,
            unit_price=str(line.unit_price),
            currency=line.currency,
            quantity=str(line.quantity),
            line_total=str(line.line_total),
        )
        db.add(row)
        rows.append(row)
    if rows:
        db.flush()
    return rows


def compute_totals(ticket_subtotal: Decimal, priced: PricedSelections, *, currency: str) -> dict:
    """The authoritative quote/checkout total. Discount and tax are always 0 — neither concept exists anywhere
    else in this codebase today (verified: no discount/coupon/tax model or field exists for events), so this
    returns honest zeros rather than inventing a calculation. See the Phase 2.8 report for this limitation."""
    meal_subtotal = priced.meal_subtotal
    accommodation_subtotal = priced.accommodation_subtotal
    discount = ZERO
    tax = ZERO
    grand_total = ticket_subtotal + meal_subtotal + accommodation_subtotal - discount + tax
    return {
        "ticket_subtotal": money_to_float(ticket_subtotal),
        "meal_subtotal": money_to_float(meal_subtotal),
        "accommodation_subtotal": money_to_float(accommodation_subtotal),
        "discount": money_to_float(discount),
        "tax": money_to_float(tax),
        "grand_total": money_to_float(grand_total),
        "currency": currency,
        "items": [
            {
                "option_type": line.option_type,
                "option_id": line.option_id,
                "name": line.name,
                "unit_price": money_to_float(line.unit_price),
                "quantity": line.quantity,
                "line_total": money_to_float(line.line_total),
                "currency": line.currency,
            }
            for line in priced.all_lines
        ],
    }


def option_availability(db: Session, event, option_type: str, option: dict) -> dict:
    """Availability info for ONE option, for API responses (Section 9). Never exposes internal DB details —
    only the counted/derived figures."""
    capacity = option.get("capacity")
    taken = _count_active_selections(db, event.id, option_type, option["id"]) if capacity is not None else None
    remaining = None if capacity is None else max(0, int(capacity) - taken)
    return {
        "capacity": capacity,
        "reserved_count": taken,
        "remaining_capacity": remaining,
        "sold_out": remaining is not None and remaining <= 0,
    }
