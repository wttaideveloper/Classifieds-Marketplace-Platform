"""Walk-in registration (Phase 2.5): an organizer registers, and optionally admits, an attendee at the venue.

A walk-in is a NORMAL ``EventRegistration`` (confirmed, its own QR value, in the normal attendee list and
dashboard). The only difference is ``registration_source = "walk_in"``. Everything else is reused:

  * event status rules ............ the same closed/not-published checks as online registration/checkout
  * lifecycle ..................... ``get_event_lifecycle_state`` (a finished event takes no walk-ins)
  * ticket + price ................ ``_resolve_ticket`` / ``_ticket_effective_price`` (the checkout's own)
  * duplicate protection .......... one active registration per event + lower(email), same 409 as online
  * capacity ...................... ``_seats_taken`` (Phase 2.1 accounting) + live waitlist payment offers, then
                                    the ticket-type and max_participants limits the online flows apply
  * custom answers ................ the form-config ``validate_custom_values`` behind GET /registration-form
  * check-in ...................... ``check_in_service`` and the Phase 2.4 ``check_in_session_service``
  * audit ......................... ``_log_audit`` (staged, never committed on its own)

The public registration WINDOW is deliberately not applied: the organizer is standing at the venue. Status,
lifecycle, capacity, ticket, duplicate and payment rules all still are.

Payment. Paid registration today is a stub (checkout marks the order confirmed with no gateway) and the
backend has no offline/manual payment mode and no endpoint that moves a payment from pending to confirmed.
So a walk-in never records a payment it did not receive: for a priced ticket it creates an order whose
``payment_status`` is ``pending`` (the documented vocabulary; the attendee list shows ``pending``, revenue
excludes it) and does NOT check the attendee in. Only a ticket that costs nothing is complete.

Atomicity. The registration, its order, the walk-in audit row, the event check-in and the session check-in are
staged in ONE transaction and committed once; any failure rolls all of it back.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.event_aux_models import EventOrder, EventRegistration, EventWaitlist
from app.models.event_model import Event
from app.schemas.event_schema import EventCheckInRequest
from app.schemas.event_session_attendance_schema import EventSessionCheckInRequest
from app.schemas.event_walk_in_schema import (
    EventWalkInResponse,
    WalkInCheckIn,
    WalkInPayment,
    WalkInSessionCheckIn,
    WalkInTicket,
)
from app.services.event_attendee_service import get_attendee_service
from app.services.event_dashboard_service import _parse_capacity
from app.services.event_meal_service import validated_selections
from app.services.event_form_config_service import (
    _iter_custom_fields,
    get_event_form_configuration_service,
    validate_custom_values,
)
from app.services.event_service import (
    _actor_id,
    _event_is_paid,
    _get_event_or_404,
    _log_audit,
    _resolve_ticket,
    _seats_taken,
    _ticket_effective_price,
    _validate_session_for_event,
    check_in_service,
)
from app.services.event_session_attendance_service import check_in_session_service
from app.utils.event_payments import PAYMENT_FREE, PAYMENT_PAID, PAYMENT_PENDING, derive_payment_status, money_to_float, parse_money
from app.utils.event_utils import get_event_lifecycle_state

WALK_IN_SOURCE = "walk_in"
# The event states online registration / checkout refuse (create_registration_service, create_event_checkout_service).
CLOSED_EVENT_STATUSES = ("cancelled", "completed", "archived", "suspended")
# Bookkeeping keys the group-registration flow stores next to real answers; never a walk-in answer.
_RESERVED_ANSWER_KEYS = frozenset({"group_members", "group_size", "group_leader"})
_PENDING_NOTE = (
    "Payment has not been collected or recorded: the backend has no offline/manual payment mode, so the order stays "
    "pending and nothing is marked paid."
)


# ---------------------------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------------------------


def _assert_event_open_for_walk_in(event) -> None:
    """Same status rules as online registration; a finished event (lifecycle) takes no walk-ins either.
    The customer registration window is intentionally NOT checked."""
    if event.status in CLOSED_EVENT_STATUSES:
        raise HTTPException(status_code=400, detail=f"Walk-in registrations are closed — event is {event.status}")
    if event.status != "published":
        raise HTTPException(status_code=400, detail=f"Event not open for registration (status: {event.status})")
    if get_event_lifecycle_state(event) == "finished":
        raise HTTPException(status_code=400, detail="Walk-in registrations are closed — the event has finished")


def _resolve_walk_in_ticket(event, ticket_type_id: str | None, paid: bool):
    """The ticket the walk-in buys. Reuses the checkout's lookup; a ticket type must belong to THIS event."""
    if ticket_type_id:
        ticket = _resolve_ticket(event, ticket_type_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket type not found")
        return ticket
    if paid and any(isinstance(t, dict) for t in (event.ticket_types or [])):
        raise HTTPException(status_code=400, detail="ticket_type_id is required for this event")
    return None


def _amount_due(event, ticket) -> tuple[Decimal, str]:
    """Order total (one seat) and currency from the checkout's own price rules; never recomputed here."""
    amount = parse_money(_ticket_effective_price(ticket or {}, event))
    if amount is None or amount < 0:
        raise HTTPException(status_code=400, detail="The ticket price is not configured correctly")
    currency = (ticket or {}).get("currency") or event.currency or "INR"
    return amount, currency


def _registration_form_sections(db: Session, event, current_user) -> list[dict]:
    """The event's registration form (pinned form version, or the legacy default): what GET /registration-form serves."""
    try:
        return get_event_form_configuration_service(db, event.id, current_user)["sections"]
    except HTTPException as exc:
        if exc.status_code == 503:  # the legacy default form is not seeded: there are no questions to ask
            return []
        raise


def _validated_answers(db: Session, event, current_user, answers: dict | None) -> dict:
    """Answers checked with the existing form validator (unknown ids, types, options, pattern, required).

    The validator returns early when no answers are sent, which would skip its required-field check, so that
    case is checked here. Nothing outside the form's own field ids is ever stored.
    """
    answers = dict(answers or {})
    for key in answers:
        if key in _RESERVED_ANSWER_KEYS:
            raise HTTPException(status_code=400, detail=f"Unknown custom field_id: {key}")
    sections = _registration_form_sections(db, event, current_user)
    items = [{"field_id": str(key), "value": value} for key, value in answers.items()]
    validate_custom_values(items, sections)
    if not items:
        for field in _iter_custom_fields(sections):
            if field.get("required"):
                raise HTTPException(status_code=400, detail=f"Required custom field missing: {field.get('label')}")
    return {item["field_id"]: item["value"] for item in items}


def _assert_not_already_registered(db: Session, event_id: UUID, email: str) -> None:
    active = db.execute(
        sa.select(EventRegistration.id).where(
            EventRegistration.event_id == event_id,
            sa.func.lower(EventRegistration.participant_email) == email,
            EventRegistration.status.in_(["confirmed", "attended"]),
        ).limit(1)
    ).first()
    if active:  # same status and message as online registration
        raise HTTPException(
            status_code=409,
            detail="Already registered for this event. Cancel your existing registration before registering again.",
        )


# ---------------------------------------------------------------------------------------------
# capacity
# ---------------------------------------------------------------------------------------------


def live_offer_count(db: Session, event_id: UUID) -> int:
    """Seats promised to live waitlist payment offers: the exact rule ``_try_promote_from_waitlist`` and the
    dashboard use (``payment_pending`` and not past ``payment_offer_expires_at``)."""
    return db.execute(
        sa.select(sa.func.count(EventWaitlist.id)).where(
            EventWaitlist.event_id == event_id,
            EventWaitlist.status == "payment_pending",
            sa.or_(EventWaitlist.payment_offer_expires_at.is_(None), EventWaitlist.payment_offer_expires_at > datetime.utcnow()),
        )
    ).scalar_one()


def _assert_capacity(db: Session, event, ticket_type_id: str | None, ticket) -> None:
    """One seat. Reject (never waitlist) when it does not fit.

    Event capacity uses the Phase 2.1 accounting (each seat once, multi-quantity orders included) PLUS live waitlist
    offers, so a seat held for a person who is paying is not handed to a walk-in. The ticket-type and
    max_participants limits are the ones the checkout / free registration apply.
    """
    capacity = _parse_capacity(event.capacity)
    if capacity is not None and _seats_taken(db, event.id) + live_offer_count(db, event.id) + 1 > capacity:
        raise HTTPException(
            status_code=400,
            detail=f"Event is at full capacity ({capacity} participants). Walk-in registrations do not use the waitlist.",
        )
    if ticket_type_id and ticket and ticket.get("capacity"):
        try:
            ticket_cap = int(float(str(ticket["capacity"]).strip()))
        except (ValueError, OverflowError):
            ticket_cap = None
        if ticket_cap is not None and _seats_taken(db, event.id, ticket_type_id) + 1 > ticket_cap:
            raise HTTPException(status_code=400, detail=f"Ticket type at capacity ({ticket_cap})")
    max_participants = _parse_capacity(event.max_participants)
    if max_participants is not None:
        active = db.execute(
            sa.select(sa.func.count(EventRegistration.id)).where(
                EventRegistration.event_id == event.id, EventRegistration.status.in_(["confirmed", "attended"])
            )
        ).scalar_one()
        if active + 1 > max_participants:
            raise HTTPException(status_code=400, detail=f"Maximum participants reached ({max_participants})")


# ---------------------------------------------------------------------------------------------
# the operation
# ---------------------------------------------------------------------------------------------


def new_qr_code() -> str:
    """The QR value every registration gets (same formula as online registration and checkout)."""
    return str(uuid.uuid4())[:12].upper()


def create_walk_in_service(db: Session, event_id: UUID, payload, current_user: dict | None = None) -> EventWalkInResponse:
    event = _get_event_or_404(db, event_id)
    _assert_event_open_for_walk_in(event)
    paid = _event_is_paid(event)
    email = payload.participant_email.strip().lower()

    if payload.session_id:
        _validate_session_for_event(event, payload.session_id)  # 400: not one of this event's own sessions
    ticket = _resolve_walk_in_ticket(event, payload.ticket_type_id, paid)
    answers = _validated_answers(db, event, current_user, payload.custom_fields)
    meal_selections = validated_selections(event, payload.meal_selections)  # the same validator online registration uses
    amount, currency = _amount_due(event, ticket) if paid else (None, None)
    payment_complete = (not paid) or amount == 0

    check_in_result = WalkInCheckIn(requested=payload.check_in, performed=False)
    session_result = WalkInSessionCheckIn(requested=bool(payload.session_id), session_id=payload.session_id, performed=False)
    try:
        db.query(Event).filter(Event.id == event_id).with_for_update().first()  # serializes the duplicate/capacity checks
        _assert_not_already_registered(db, event_id, email)
        _assert_capacity(db, event, payload.ticket_type_id, ticket)

        order = None
        if paid:  # order first, then registration: the pairing the attendee list and refund/check-in code rely on
            order = EventOrder(
                event_id=event_id,
                participant_name=payload.participant_name,
                participant_email=email,
                ticket_type_id=payload.ticket_type_id,
                quantity="1",
                amount=str(float(amount)),
                currency=currency,
                # A zero-price ticket owes nothing. Otherwise NO payment has been received, so it stays pending.
                payment_status="confirmed" if payment_complete else "pending",
                status="confirmed",
                payment_provider=sa.null(),  # no provider handled anything: do not claim "marketplace"
            )
            db.add(order)
            db.flush()

        reg = EventRegistration(
            event_id=event_id,
            participant_name=payload.participant_name,
            participant_email=email,
            custom_fields=answers,
            ticket_type_id=payload.ticket_type_id,
            status="confirmed",
            qr_code=new_qr_code(),
            registration_source=WALK_IN_SOURCE,
            meal_selections=meal_selections,
        )
        db.add(reg)
        db.flush()

        payment_status = derive_payment_status(order.status, order.payment_status) if order is not None else PAYMENT_FREE
        _log_audit(
            db, event_id, "walk_in_registration", None,
            {
                "registration_id": str(reg.id), "participant_email": email, "participant_name": payload.participant_name,
                "ticket_type_id": payload.ticket_type_id, "registration_source": WALK_IN_SOURCE, "status": "confirmed",
                "payment_status": payment_status, "order_id": str(order.id) if order is not None else None,
                "amount": str(order.amount) if order is not None else None,
                "check_in_requested": payload.check_in, "session_id": payload.session_id,
                "meal_selections": meal_selections or [],
            },
            changed_by=_actor_id(current_user), commit=False,
        )

        # Admission goes through the existing services, staged in this same transaction (commit=False).
        # A pending payment is never admitted.
        if payload.check_in:
            if payment_complete:
                admitted = check_in_service(
                    db, event_id, EventCheckInRequest(registration_id=reg.id, method="walk_in"), current_user, commit=False)
                check_in_result = WalkInCheckIn(
                    requested=True, performed=True, checked_in_at=datetime.fromisoformat(admitted.checked_in_at) if admitted.checked_in_at else None)
            else:
                check_in_result.reason = "payment_pending"
        if payload.session_id:
            if payment_complete:
                check_in_session_service(
                    db, event_id, payload.session_id, EventSessionCheckInRequest(registration_id=reg.id, method="walk_in"),
                    current_user, commit=False)
                session_result.performed = True
            else:
                session_result.reason = "payment_pending"
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:  # a concurrent registration won the unique index (PostgreSQL: uq_event_reg_active)
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Already registered for this event. Cancel your existing registration before registering again.",
        )
    except Exception:
        db.rollback()
        raise

    if payment_complete:  # the usual confirmation (with the QR); not sent while the payment is pending
        try:
            from app.services.notification_triggers import notify_registration_confirmation

            notify_registration_confirmation(db, event, reg)
        except Exception:
            pass

    registration = get_attendee_service(db, event_id, reg.id)
    message = "Walk-in registered"
    if check_in_result.performed:
        message += " and checked in"
    elif payload.check_in:
        message += "; payment is pending, so the attendee was not checked in"
    return EventWalkInResponse(
        message=message,
        registration=registration,
        ticket=WalkInTicket(
            registration_reference=registration.registration_reference,
            qr_code=registration.registration_reference,
            ticket_type_id=registration.ticket_type_id,
            ticket_type_name=registration.ticket_type_name,
        ),
        payment=WalkInPayment(
            required=bool(paid and amount and amount > 0),
            status=payment_status if payment_status in (PAYMENT_FREE, PAYMENT_PAID, PAYMENT_PENDING) else PAYMENT_PENDING,
            amount=money_to_float(amount) if amount is not None else None,
            currency=currency,
            order_id=registration.order_id,
            note=None if payment_complete else _PENDING_NOTE,
        ),
        check_in=check_in_result,
        session_check_in=session_result,
    )
