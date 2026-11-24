"""Service bookings: create, cancel, decide, change status, payment status, history and the answers (docs/service-bookings.md).

Payment is a status only (`payment_status` on the booking): nothing here talks to the payments module. A refund is the same: the booking records
the refund percent and amount and moves `payment_status` to refunded / partially_refunded.

Who may do what:
  customer      books for themselves, sees their own bookings, cancels (instantly inside the free window, otherwise a request an admin decides)
  admin         (Enterprise Admin of the owning tenant) sees and manages every booking of the enterprise, books for a customer, decides cancellation requests
  provider      sees and updates (start, complete, no-show) the bookings assigned to them
  super_admin   everything
Everyone else gets 404 for a booking that is not theirs.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mskit.settings import settings
from booking_service.models.enterprise_model import Enterprise
from booking_service.models.service_booking_model import ServiceBooking, ServiceBookingEvent
from booking_service.models.service_model import Service
from booking_service.services import service_booking_notifications as notify
from booking_service.services import service_booking_slots as slots

logger = logging.getLogger(__name__)

TERMINAL = ("completed", "cancelled", "no_show", "expired")
ADMIN_TRANSITIONS = {
    "pending": ("confirmed", "cancelled"),
    "confirmed": ("in_progress", "completed", "no_show", "cancelled"),
    "in_progress": ("completed",),
}
PROVIDER_TARGETS = ("in_progress", "completed", "no_show")
CHECK_IN_LEAD = timedelta(minutes=15)


def _err(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"message": message, "code": code, **extra})


# ---------------------------------------------------------------- who is calling

@dataclass
class Actor:
    id: UUID
    role: str                      # customer | admin | provider | super_admin
    tenant_id: UUID | None = None
    name: str | None = None
    email: str | None = None
    token: str | None = None            # the caller's bearer token (the identity lookup of the enterprise's admins needs one)

    @property
    def is_staff(self) -> bool:
        return self.role in ("admin", "provider", "super_admin")


def actor_from(db: Session, user: dict, access_token: str | None = None) -> Actor:
    from mskit.auth.auth_context import resolve_auth_tenant_id_with_db
    from mskit.auth.access import resolve_catalog_role

    try:
        uid = UUID(str(user.get("id")))
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid user identity")
    role = resolve_catalog_role(user) or "customer"
    tenant = None
    if role in ("admin", "provider"):
        try:
            tenant = UUID(str(resolve_auth_tenant_id_with_db(db, user, access_token=access_token)))
        except (ValueError, TypeError):
            raise _err(403, "TENANT_REQUIRED", "Authenticated tenant identity required")
    name = " ".join(str(user.get("name") or "").split())
    return Actor(id=uid, role=role, tenant_id=tenant, name=None if (not name or "@" in name) else name, email=(user.get("email") or None), token=access_token)


def _service_tenant(db: Session, service: Service) -> UUID | None:
    if service.tenant_id:
        return service.tenant_id
    enterprise = service.enterprise or db.get(Enterprise, service.enterprise_id)
    return getattr(enterprise, "tenant_id", None)


def _may_manage(db: Session, actor: Actor, service: Service) -> bool:
    if actor.role == "super_admin":
        return True
    if actor.role in ("admin", "provider") and _service_tenant(db, service) == actor.tenant_id:
        return actor.role == "admin" or service.provider_user_id == actor.id
    return False


def _load(db: Session, booking_id: UUID) -> ServiceBooking:
    booking = db.get(ServiceBooking, booking_id)
    if booking is None:
        raise _err(404, "BOOKING_NOT_FOUND", "Booking not found")
    return booking


def can_view(actor: Actor, booking: ServiceBooking) -> bool:
    if actor.role == "super_admin":
        return True
    if actor.role == "admin":
        return booking.tenant_id == actor.tenant_id
    if actor.role == "provider":
        return booking.tenant_id == actor.tenant_id and booking.provider_user_id == actor.id
    return booking.customer_user_id == actor.id


def get_for(db: Session, actor: Actor, booking_id: UUID) -> ServiceBooking:
    booking = _load(db, booking_id)
    if not can_view(actor, booking):
        raise _err(404, "BOOKING_NOT_FOUND", "Booking not found")
    return booking


# ---------------------------------------------------------------- history and status

def _log(db: Session, booking: ServiceBooking, action: str, actor: Actor | None, *, from_status=None, to_status=None, reason=None, detail=None) -> None:
    db.add(ServiceBookingEvent(
        booking_id=booking.id, action=action, from_status=from_status, to_status=to_status, actor_id=actor.id if actor else None,
        actor_role=actor.role if actor else "system", actor_name=actor.name if actor else None, reason=reason, detail=detail,
    ))


def _move(db: Session, booking: ServiceBooking, new_status: str, actor: Actor | None, action: str, reason: str | None = None, detail: dict | None = None) -> None:
    previous = booking.status
    booking.status = new_status
    booking.updated_at = slots.now_utc()
    _log(db, booking, action, actor, from_status=previous, to_status=new_status, reason=reason, detail=detail)


def expire_stale_holds(db: Session, now: datetime | None = None) -> int:
    """Unpaid bookings whose hold ran out give their place back (status `expired`)."""
    now = now or slots.now_utc()
    rows = db.query(ServiceBooking).filter(ServiceBooking.status == "pending", ServiceBooking.payment_status == "unpaid",
                                           ServiceBooking.hold_expires_at.isnot(None), ServiceBooking.hold_expires_at <= now).all()
    for booking in rows:
        _move(db, booking, "expired", None, "expired", "The payment was not made in time")
        booking.hold_expires_at = None
    if rows:
        db.commit()
    return len(rows)


def _apply_refund(booking: ServiceBooking, percent: int) -> None:
    """Record a refund on the booking (status only: no money moves here)."""
    percent = max(0, min(100, int(percent)))
    booking.refund_percent = percent
    if booking.payment_status == "paid":
        booking.refund_amount = round((booking.amount or 0) * percent / 100, 2)
        if percent >= 100:
            booking.payment_status = "refunded"
        elif percent > 0:
            booking.payment_status = "partially_refunded"
    else:
        booking.refund_amount = 0.0


# ---------------------------------------------------------------- create

def create_booking(db: Session, actor: Actor, body, idempotency_key: str | None = None) -> tuple[ServiceBooking, bool]:
    """Returns (booking, created). A repeated request with the same Idempotency-Key returns the first booking (created=False)."""
    key = (idempotency_key or "").strip()[:100] or None
    if key:
        existing = db.query(ServiceBooking).filter(ServiceBooking.created_by == actor.id, ServiceBooking.idempotency_key == key).first()
        if existing is not None:
            return existing, False
    service = db.query(Service).filter(Service.id == body.service_id, Service.is_deleted.is_(False)).with_for_update().first()   # one booking at a time per service
    if service is None:
        raise _err(404, "SERVICE_NOT_FOUND", "Service not found")
    manual = actor.is_staff
    if manual and not _may_manage(db, actor, service):
        raise _err(403, "NOT_YOUR_SERVICE", "This service belongs to another enterprise or provider")
    if not manual and (body.customer_user_id not in (None, actor.id)):
        raise _err(403, "BOOK_FOR_SELF_ONLY", "You can only book for yourself")
    reason = slots.bookable_reason(service)
    if reason:
        raise _err(409, "SERVICE_NOT_BOOKABLE", reason)

    try:
        starts = slots.parse_aware(body.starts_at)
    except ValueError as exc:
        raise _err(422, "INVALID_STARTS_AT", str(exc))
    modes = slots.delivery_modes_of(service)
    mode = body.delivery_mode or (modes[0] if len(modes) == 1 else None)
    if mode is None:
        raise _err(422, "DELIVERY_MODE_REQUIRED", "Choose how you will attend", delivery_modes=modes)
    if mode not in modes:
        raise _err(422, "DELIVERY_MODE_NOT_OFFERED", "This service is not offered that way", delivery_modes=modes)
    if body.provider_id is not None and body.provider_id != service.provider_user_id:
        raise _err(422, "PROVIDER_NOT_FOR_SERVICE", "That provider does not offer this service")
    participants = body.participants or 1
    if manual and not (body.customer_name or body.customer_user_id):
        raise _err(422, "CUSTOMER_REQUIRED", "customer_name (or customer_user_id) is required when booking for a customer")

    now = slots.now_utc()
    expire_stale_holds(db, now)
    slot = slots.find_slot(db, service, starts, now)
    if slot is None:
        raise _err(422, "SLOT_NOT_OFFERED", "That time is not one of the service's bookable slots")
    if starts <= now:
        raise _err(409, "SLOT_IN_THE_PAST", "That time has passed")
    if slot.reason in ("too_soon", "too_far") and not manual:
        raise _err(409, "SLOT_NOT_BOOKABLE", "That time is too soon or too far ahead to book", slot_reason=slot.reason)
    if slot.reason == "provider_busy":
        raise _err(409, "PROVIDER_UNAVAILABLE", "The provider is booked at that time")
    if slot.reason == "full" or participants > slot.available_seats:
        raise _err(409, "SLOT_FULL", "That slot is full", available_seats=slot.available_seats)

    customer_id = body.customer_user_id if manual else actor.id
    email = (body.customer_email if manual else actor.email) or None
    name = (body.customer_name if manual else actor.name) or None
    dup = db.query(ServiceBooking.id).filter(
        ServiceBooking.service_id == service.id, ServiceBooking.starts_at == starts, slots.holding_clause(now),
        or_(*([ServiceBooking.customer_user_id == customer_id] if customer_id else []), *([func.lower(ServiceBooking.customer_email) == email.lower()] if email else []),
            *([] if (customer_id or email) else [ServiceBooking.id.is_(None)])),
    ).first()
    if dup is not None:
        raise _err(409, "DUPLICATE_BOOKING", "There is already a booking for this person at that time", booking_id=str(dup[0]))

    amount = round(float(service.service_price or 0) * participants, 2)
    paid_up = bool(manual and body.paid and amount > 0)
    if amount > 0 and not paid_up:
        status, payment, hold = "pending", "unpaid", now + timedelta(minutes=settings.SERVICE_BOOKING_HOLD_MINUTES)
    elif amount > 0:
        status, payment, hold = "confirmed", "paid", None
    else:
        status, payment, hold = ("pending" if (service.requires_approval and not manual) else "confirmed"), "not_required", None
    enterprise = service.enterprise or db.get(Enterprise, service.enterprise_id)
    booking = ServiceBooking(
        tenant_id=_service_tenant(db, service), enterprise_id=service.enterprise_id, service_id=service.id, customer_user_id=customer_id,
        customer_name=name, customer_email=email, customer_phone=body.customer_phone if manual else None, created_by=actor.id, created_by_role=actor.role,
        provider_user_id=service.provider_user_id, provider_name=service.provider_name or service.instructor_name, delivery_mode=mode, starts_at=starts,
        ends_at=starts + timedelta(minutes=slots.duration_of(service)), time_zone=service.time_zone or slots.DEFAULT_TIME_ZONE, participants=participants,
        status=status, payment_status=payment, amount=amount, currency=service.currency, paid_at=now if paid_up else None, hold_expires_at=hold,
        notes=(body.notes or None), idempotency_key=key,
    )
    db.add(booking)
    db.flush()
    _log(db, booking, "created", actor, to_status=status, detail={"payment_status": payment, "participants": participants, "on_behalf": manual})
    if paid_up:
        _log(db, booking, "paid", actor, detail={"amount": amount})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(ServiceBooking).filter(ServiceBooking.created_by == actor.id, ServiceBooking.idempotency_key == key).first() if key else None
        if existing is not None:
            return existing, False
        raise
    db.refresh(booking)
    booking.service = service
    notify.booking_created(db, booking, service, actor.token)
    return booking, True


# ---------------------------------------------------------------- payment status (no gateway)

def mark_paid(db: Session, actor: Actor, booking_id: UUID, reference: str | None = None) -> ServiceBooking:
    booking = get_for(db, actor, booking_id)
    service = booking.service
    if actor.role == "customer":
        if not settings.service_booking_demo_payment:
            raise _err(403, "PAYMENT_NOT_AVAILABLE", "Payment is not available in the app yet: ask the clinic to record it")
    elif not _may_manage(db, actor, service) or actor.role == "provider":
        raise _err(403, "NOT_ALLOWED", "Only the Enterprise Admin can record a payment")
    expire_stale_holds(db)
    db.refresh(booking)
    if booking.status == "expired":
        raise _err(409, "HOLD_EXPIRED", "The hold on this slot ran out before the payment. Book again")
    if booking.payment_status != "unpaid":
        raise _err(409, "NOTHING_TO_PAY", "This booking does not need a payment" if booking.payment_status == "not_required" else "This booking is already paid")
    if booking.status in TERMINAL:
        raise _err(409, "BOOKING_CLOSED", f"This booking is {booking.status}")
    booking.payment_status, booking.paid_at, booking.hold_expires_at = "paid", slots.now_utc(), None
    _log(db, booking, "paid", actor, detail={"amount": booking.amount, "reference": reference})
    if booking.status == "pending" and not service.requires_approval:
        _move(db, booking, "confirmed", actor, "confirmed", "Paid")
    db.commit()
    db.refresh(booking)
    notify.booking_paid(db, booking, service, actor.token)
    return booking


# ---------------------------------------------------------------- cancellation

def cancel_decision(booking: ServiceBooking, service: Service, now: datetime | None = None) -> dict:
    """What happens if the customer cancels now: {"mode": instant | request | blocked, "reason", "refund_percent"}."""
    now = now or slots.now_utc()
    rules = slots.rules_of(service)
    if booking.status in TERMINAL:
        return {"mode": "blocked", "reason": f"This booking is {booking.status.replace('_', ' ')}", "refund_percent": 0}
    if booking.status == "cancellation_requested":
        return {"mode": "blocked", "reason": "A cancellation request is already waiting for the clinic", "refund_percent": 0}
    if booking.status == "in_progress" or now >= booking.starts_at:
        return {"mode": "blocked", "reason": "This booking has already started", "refund_percent": 0}
    if booking.status == "pending":
        return {"mode": "instant", "reason": None, "refund_percent": rules["refund_percent_before"] if booking.payment_status == "paid" else 0}
    hours_left = (booking.starts_at - now).total_seconds() / 3600
    if hours_left >= rules["free_cancellation_hours"]:
        return {"mode": "instant", "reason": None, "refund_percent": rules["refund_percent_before"]}
    if rules["allow_request_after_window"]:
        return {"mode": "request", "reason": None, "refund_percent": rules["refund_percent_after"]}
    return {"mode": "blocked", "reason": f"Free cancellation ended {rules['free_cancellation_hours']} hours before the start", "refund_percent": 0}


def policy_summary(service: Service) -> str:
    rules = slots.rules_of(service)
    hours = rules["free_cancellation_hours"]
    text = f"Free cancellation until {hours} hours before the start" if hours else "Cancel any time before the start"
    if rules["allow_request_after_window"] and hours:
        text += "; after that you can ask and the clinic decides"
    return text


def cancel_booking(db: Session, actor: Actor, booking_id: UUID, reason: str | None, refund_percent: int | None = None) -> ServiceBooking:
    booking = get_for(db, actor, booking_id)
    service = booking.service
    reason = " ".join((reason or "").split()) or None
    if actor.is_staff:
        if actor.role == "provider" or not _may_manage(db, actor, service):
            raise _err(403, "NOT_ALLOWED", "Only the Enterprise Admin can cancel a customer's booking")
        return change_status(db, actor, booking_id, "cancelled", reason, refund_percent)
    decision = cancel_decision(booking, service)
    if decision["mode"] == "blocked":
        raise _err(409, "CANCEL_NOT_ALLOWED", decision["reason"], policy=policy_summary(service))
    if decision["mode"] == "request":
        if not reason or len(reason) < 3:
            raise _err(422, "REASON_REQUIRED", "Tell the clinic why you want to cancel (at least 3 characters)")
        booking.status_before_request = booking.status
        booking.cancel_reason, booking.cancellation_requested_at = reason, slots.now_utc()
        _move(db, booking, "cancellation_requested", actor, "cancellation_requested", reason)
        db.commit()
        db.refresh(booking)
        notify.cancellation_requested(db, booking, service, actor.token)
        return booking
    booking.cancel_reason, booking.cancelled_at, booking.cancelled_by, booking.cancelled_by_role = reason, slots.now_utc(), actor.id, "customer"
    booking.hold_expires_at = None
    _apply_refund(booking, decision["refund_percent"])
    _move(db, booking, "cancelled", actor, "cancelled", reason, {"refund_percent": booking.refund_percent})
    db.commit()
    db.refresh(booking)
    notify.booking_cancelled(db, booking, service, by_customer=True, access_token=actor.token)
    return booking


def decide_cancellation(db: Session, actor: Actor, booking_id: UUID, approve: bool, reason: str | None, refund_percent: int | None = None) -> ServiceBooking:
    booking = get_for(db, actor, booking_id)
    service = booking.service
    if actor.role not in ("admin", "super_admin") or not _may_manage(db, actor, service):
        raise _err(403, "NOT_ALLOWED", "Only the Enterprise Admin can decide a cancellation request")
    if booking.status != "cancellation_requested":
        raise _err(409, "NO_REQUEST", "There is no cancellation request waiting on this booking")
    reason = " ".join((reason or "").split()) or None
    now = slots.now_utc()
    if approve:
        booking.cancelled_at, booking.cancelled_by, booking.cancelled_by_role = now, actor.id, actor.role
        booking.decision_reason = reason
        booking.hold_expires_at = None
        _apply_refund(booking, refund_percent if refund_percent is not None else slots.rules_of(service)["refund_percent_after"])
        _move(db, booking, "cancelled", actor, "cancellation_approved", reason, {"refund_percent": booking.refund_percent})
        db.commit()
        db.refresh(booking)
        notify.booking_cancelled(db, booking, service, by_customer=False)
        return booking
    if not reason or len(reason) < 3:
        raise _err(422, "REASON_REQUIRED", "A reason is required to reject a cancellation request (at least 3 characters)")
    booking.decision_reason = reason
    _move(db, booking, booking.status_before_request or "confirmed", actor, "cancellation_rejected", reason)
    booking.status_before_request = None
    db.commit()
    db.refresh(booking)
    notify.cancellation_rejected(db, booking, service)
    return booking


# ---------------------------------------------------------------- admin status changes

def allowed_next(booking: ServiceBooking, actor: Actor) -> list[str]:
    options = list(ADMIN_TRANSITIONS.get(booking.status, ()))
    if actor.role == "provider":
        options = [o for o in options if o in PROVIDER_TARGETS]
    return options


def change_status(db: Session, actor: Actor, booking_id: UUID, new_status: str, reason: str | None = None, refund_percent: int | None = None) -> ServiceBooking:
    booking = get_for(db, actor, booking_id)
    service = booking.service
    if not actor.is_staff or not _may_manage(db, actor, service):
        raise _err(403, "NOT_ALLOWED", "Only the clinic's staff can change a booking's status")
    if booking.status == "cancellation_requested":
        raise _err(409, "DECIDE_THE_REQUEST", "This booking has a cancellation request: approve or reject it")
    allowed = allowed_next(booking, actor)
    if new_status not in allowed:
        raise _err(409, "INVALID_TRANSITION", f"A {booking.status.replace('_', ' ')} booking cannot become {new_status.replace('_', ' ')}", allowed=allowed)
    reason = " ".join((reason or "").split()) or None
    now = slots.now_utc()
    if new_status == "confirmed" and (booking.amount or 0) > 0 and booking.payment_status != "paid":
        raise _err(409, "PAYMENT_REQUIRED", "This booking is not paid yet: record the payment first")
    if new_status == "in_progress" and now < booking.starts_at - CHECK_IN_LEAD:
        raise _err(409, "NOT_STARTED", "It is too early to start this booking")
    if new_status in ("completed", "no_show") and now < booking.starts_at:
        raise _err(409, "NOT_STARTED", "This booking has not started yet")
    if new_status == "cancelled":
        if not reason or len(reason) < 3:
            raise _err(422, "REASON_REQUIRED", "A reason is required to cancel a booking (at least 3 characters)")
        booking.cancel_reason, booking.cancelled_at, booking.cancelled_by, booking.cancelled_by_role = reason, now, actor.id, actor.role
        booking.hold_expires_at = None
        _apply_refund(booking, refund_percent if refund_percent is not None else 100)     # the clinic cancelled: a full refund unless it says otherwise
    if new_status == "in_progress":
        booking.checked_in_at = now
    if new_status == "completed":
        booking.completed_at = now
    _move(db, booking, new_status, actor, new_status, reason, {"refund_percent": booking.refund_percent} if new_status == "cancelled" else None)
    db.commit()
    db.refresh(booking)
    {"confirmed": lambda: notify.booking_confirmed(db, booking, service), "cancelled": lambda: notify.booking_cancelled(db, booking, service, by_customer=False),
     "completed": lambda: notify.completed(db, booking, service), "no_show": lambda: notify.no_show(db, booking, service)}.get(new_status, lambda: None)()
    return booking


# ---------------------------------------------------------------- the answers

def _venue(service: Service) -> dict | None:
    location = service.location
    if location is None:
        return {"name": None, "instructions": service.venue_instructions} if service.venue_instructions else None
    return {
        "name": location.location_name, "line1": location.address_line_1, "line2": location.address_line_2, "city": location.city, "state": location.state,
        "postal_code": location.postal_code, "country": location.country, "lat": location.latitude, "lng": location.longitude, "phone": location.phone,
        "instructions": service.venue_instructions,
    }


def _meeting(booking: ServiceBooking, service: Service, staff: bool, now: datetime) -> dict | None:
    joins = booking.starts_at - timedelta(minutes=settings.SERVICE_BOOKING_JOIN_MINUTES_BEFORE)
    open_now = booking.status in ("confirmed", "in_progress") and joins <= now <= booking.ends_at + timedelta(minutes=30)
    show = bool(service.meeting_url) and (staff or open_now)
    return {
        "provider": service.meeting_provider, "joins_at": slots.to_local_iso(joins, slots.tz_of(service)), "available": bool(service.meeting_url) and open_now,
        "url": service.meeting_url if show else None, "passcode": service.meeting_passcode if show else None, "instructions": service.meeting_instructions if show else None,
    }


def _join_state(booking: ServiceBooking, service: Service, now: datetime) -> tuple[bool, str | None]:
    if booking.delivery_mode == "in_person":
        return False, None
    if booking.status not in ("confirmed", "in_progress"):
        return False, "This booking is not confirmed"
    if not service.meeting_url:
        return False, "The clinic has not added the meeting link yet"
    joins = booking.starts_at - timedelta(minutes=settings.SERVICE_BOOKING_JOIN_MINUTES_BEFORE)
    if now < joins:
        return False, f"The meeting link opens {settings.SERVICE_BOOKING_JOIN_MINUTES_BEFORE} minutes before the start"
    if now > booking.ends_at + timedelta(minutes=30):
        return False, "This session has ended"
    return True, None


def booking_view(db: Session, booking: ServiceBooking, actor: Actor, *, detail: bool = True) -> dict:
    service = booking.service
    tz = slots.tz_of(service)
    now = slots.now_utc()
    staff = actor.is_staff
    enterprise = service.enterprise
    decision = cancel_decision(booking, service, now)
    can_join, join_reason = _join_state(booking, service, now)
    row = {
        "id": booking.id, "status": booking.status, "payment_status": booking.payment_status, "service_id": booking.service_id, "service_name": service.service_name,
        "provider_id": booking.provider_user_id, "provider_name": booking.provider_name, "enterprise_id": booking.enterprise_id,
        "enterprise_name": getattr(enterprise, "business_short_name", None), "delivery_mode": booking.delivery_mode,
        "starts_at": slots.to_local_iso(booking.starts_at, tz), "ends_at": slots.to_local_iso(booking.ends_at, tz), "timezone": booking.time_zone,
        "participants": booking.participants, "amount": booking.amount, "currency": booking.currency,
        "can_cancel": decision["mode"] != "blocked", "cancel_mode": decision["mode"] if decision["mode"] != "blocked" else None,
        "can_reschedule": False, "can_join": can_join, "can_check_in": False,
        "customer": {"user_id": booking.customer_user_id, "name": booking.customer_name, "email": booking.customer_email, "phone": booking.customer_phone},
        "created_at": slots.to_local_iso(booking.created_at, tz),
    }
    if not detail:
        return row
    row.update({
        "duration_minutes": slots.duration_of(service),
        "venue": _venue(service) if booking.delivery_mode in ("in_person", "hybrid") else None,
        "meeting": _meeting(booking, service, staff, now) if booking.delivery_mode in ("virtual", "hybrid") else None,
        "cancel_blocked_reason": decision["reason"], "join_blocked_reason": join_reason, "cancellation_policy_summary": policy_summary(service),
        "cancellation_policy": service.cancellation_policy, "hold_expires_at": slots.to_local_iso(booking.hold_expires_at, tz),
        "cancellation": {
            "requested_at": slots.to_local_iso(booking.cancellation_requested_at, tz), "reason": booking.cancel_reason, "decision_reason": booking.decision_reason,
            "cancelled_at": slots.to_local_iso(booking.cancelled_at, tz), "cancelled_by_role": booking.cancelled_by_role,
            "refund_percent": booking.refund_percent, "refund_amount": booking.refund_amount,
        },
        "payment_id": None, "qr_payload": None, "notes": booking.notes if staff or booking.created_by == actor.id else None,
        "history": [
            {"action": e.action, "from_status": e.from_status, "to_status": e.to_status, "actor_role": e.actor_role, "actor_name": e.actor_name if staff else None,
             "reason": e.reason, "at": slots.to_local_iso(e.created_at, tz)} for e in booking.history
        ],
    })
    if staff:
        row["allowed_statuses"] = allowed_next(booking, actor)
        row["created_by_role"] = booking.created_by_role
    return row


# ---------------------------------------------------------------- lists

def _paged(query, page: int, page_size: int):
    total = query.count()
    return query.offset((page - 1) * page_size).limit(page_size).all(), total


def _apply_when(query, when: str | None, now: datetime):
    if when == "upcoming":
        return query.filter(ServiceBooking.starts_at >= now, ServiceBooking.status.in_(slots.ACTIVE_STATUSES)).order_by(ServiceBooking.starts_at.asc())
    if when == "past":
        return query.filter(or_(ServiceBooking.starts_at < now, ServiceBooking.status.in_(TERMINAL))).order_by(ServiceBooking.starts_at.desc())
    return query.order_by(ServiceBooking.starts_at.desc())


def list_mine(db: Session, actor: Actor, *, status: str | None, when: str | None, page: int, page_size: int) -> dict:
    expire_stale_holds(db)
    query = db.query(ServiceBooking).filter(ServiceBooking.customer_user_id == actor.id)
    if status:
        query = query.filter(ServiceBooking.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    now = slots.now_utc()
    rows, total = _paged(_apply_when(query, when, now), page, page_size)
    return {"items": [booking_view(db, b, actor, detail=False) for b in rows], "total": total, "page": page, "page_size": page_size}


def list_for_enterprise(
    db: Session, actor: Actor, enterprise_id: UUID, *, status: str | None = None, service_id: UUID | None = None, provider_id: UUID | None = None,
    payment_status: str | None = None, q: str | None = None, date_from: date | None = None, date_to: date | None = None, when: str | None = None,
    only_requests: bool = False, page: int = 1, page_size: int = 20,
) -> dict:
    enterprise = db.query(Enterprise).filter(Enterprise.id == enterprise_id, Enterprise.is_deleted.is_(False)).first() if hasattr(Enterprise, "is_deleted") else db.get(Enterprise, enterprise_id)
    if enterprise is None or not actor.is_staff or (actor.role != "super_admin" and enterprise.tenant_id != actor.tenant_id):
        raise _err(404, "ENTERPRISE_NOT_FOUND", "Enterprise not found")
    expire_stale_holds(db)
    query = db.query(ServiceBooking).join(Service, Service.id == ServiceBooking.service_id).filter(ServiceBooking.enterprise_id == enterprise_id)
    if actor.role == "provider":
        query = query.filter(ServiceBooking.provider_user_id == actor.id)
    if only_requests:
        query = query.filter(ServiceBooking.status == "cancellation_requested")
    elif status:
        query = query.filter(ServiceBooking.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    if service_id:
        query = query.filter(ServiceBooking.service_id == service_id)
    if provider_id:
        query = query.filter(ServiceBooking.provider_user_id == provider_id)
    if payment_status:
        query = query.filter(ServiceBooking.payment_status == payment_status)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        query = query.filter(or_(func.lower(ServiceBooking.customer_name).like(like), func.lower(ServiceBooking.customer_email).like(like),
                                 func.lower(Service.service_name).like(like), func.lower(ServiceBooking.provider_name).like(like)))
    if date_from:
        query = query.filter(ServiceBooking.starts_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        query = query.filter(ServiceBooking.starts_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
    now = slots.now_utc()
    query = query.order_by(ServiceBooking.cancellation_requested_at.asc()) if only_requests else _apply_when(query, when, now)
    rows, total = _paged(query, page, page_size)
    return {"items": [booking_view(db, b, actor, detail=False) for b in rows], "total": total, "page": page, "page_size": page_size}
