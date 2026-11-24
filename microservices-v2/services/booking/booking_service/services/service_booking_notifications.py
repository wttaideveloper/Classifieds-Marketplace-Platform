"""Service booking notifications (push + in-app feed, through the platform pipeline), and the reminder / hold-expiry scheduler.

Customer categories (docs/service-bookings.md has the payloads):
    service_booking_pending_approval       the booking waits for an admin to confirm it
    service_booking_confirmed              confirmed (automatically, after payment, or by an admin)
    service_booking_cancelled              cancelled (by the customer, or approved / done by staff)
    service_booking_cancellation_rejected  the admin did not approve a cancellation request
    service_booking_reminder               24 h and 1 h before the start (SERVICE_BOOKING_REMINDER_HOURS)
    service_booking_completed              marked completed
    service_booking_no_show                marked no-show
Staff (the service's provider and the enterprise's admins; web / admin app):
    service_booking_new                    a booking is waiting for approval
    service_booking_cancellation_requested a customer asked to cancel
    service_booking_cancelled              a customer cancelled

`data` is a flat map of strings: category, booking_id, service_id, service_name, entity_type = "service_booking", entity_id, starts_at (ISO-8601 with the
service's UTC offset), delivery_mode, status. A free-text `reason` is in the in-app feed only, never in the push. Everything here is best effort and runs after
the change is saved: a problem sending never fails the API call.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from mskit.settings import settings
from mskit.clients import claim_dedupe_key, release_dedupe_key  # notifications service
from booking_service.services import service_booking_slots as slots

logger = logging.getLogger(__name__)


def _when(booking, service) -> str:
    return booking.starts_at.replace(tzinfo=timezone.utc).astimezone(slots.tz_of(service)).strftime("%a %d %b, %H:%M")


def payload(booking, service, category: str, **extra) -> dict[str, str]:
    data = {
        "category": category, "booking_id": booking.id, "service_id": booking.service_id, "service_name": getattr(service, "service_name", None),
        "entity_type": "service_booking", "entity_id": booking.id, "starts_at": slots.to_local_iso(booking.starts_at, slots.tz_of(service)),
        "delivery_mode": booking.delivery_mode, "status": booking.status, **extra,
    }
    return {k: str(v) for k, v in data.items() if v is not None and v != ""}


def _deliver(db: Session, *, users, category: str, title: str, body: str, data: dict, key: str, tenant_id=None) -> bool:
    ids = []
    for u in dict.fromkeys(users or []):
        try:
            ids.append(UUID(str(u)))
        except (ValueError, TypeError):
            continue
    if not ids:
        return False
    try:
        if not claim_dedupe_key(db, key):
            return False
        from mskit.clients import create_automatic_notification

        create_automatic_notification(db, title=title, message=body, category=category, user_ids=ids, tenant_id=tenant_id, metadata=data, channels=["in_app", "push"])
        return True
    except Exception:
        logger.exception("service booking notification %s failed", category)
        try:
            db.rollback()
            release_dedupe_key(db, key)
        except Exception:
            pass
        return False


def _stamp(booking) -> str:
    return (booking.updated_at or datetime.utcnow()).replace(microsecond=0).isoformat()


def to_customer(db: Session, booking, service, category: str, title: str, body: str, *, reason: str | None = None) -> bool:
    if not booking.customer_user_id:
        return False
    data = payload(booking, service, category, **({"reason": reason} if reason else {}))
    return _deliver(db, users=[booking.customer_user_id], category=category, title=title, body=body, data=data, tenant_id=booking.tenant_id,
                    key=f"service_booking:{booking.id}:{category}:{_stamp(booking)}")


def to_staff(db: Session, booking, service, category: str, title: str, body: str, *, reason: str | None = None, access_token: str | None = None) -> bool:
    """The service's provider and the enterprise's admins. Finding the admins is an identity-service lookup that needs the caller's bearer token (the customer
    who booked or cancelled): without it the lookup fails and only the provider is told, so every caller passes the token it has."""
    users = [booking.provider_user_id] if booking.provider_user_id else []
    try:
        from booking_service.services.booking_recipients import resolve_enterprise_admin_user_ids

        found, _tenant = resolve_enterprise_admin_user_ids(db, service, access_token=access_token)
        if not found:
            logger.warning("service booking %s: no Enterprise Admin found to notify (token supplied: %s)", booking.id, bool(access_token))
        users += [str(u) for u in found]
    except Exception:
        logger.warning("could not list the admins of service %s (token supplied: %s)", booking.service_id, bool(access_token), exc_info=True)
    data = payload(booking, service, category, **({"reason": reason} if reason else {}))
    return _deliver(db, users=users, category=category, title=title, body=body, data=data, tenant_id=booking.tenant_id,
                    key=f"service_booking_staff:{booking.id}:{category}:{_stamp(booking)}")


# ---- what each change tells people --------------------------------------------------------------------------------------------------

def booking_created(db: Session, booking, service, access_token: str | None = None) -> None:
    name = service.service_name
    if booking.status == "confirmed":
        to_customer(db, booking, service, "service_booking_confirmed", "Booking confirmed", f"Your booking for {name} is confirmed for {_when(booking, service)}.")
    elif booking.status == "pending" and booking.payment_status != "unpaid":
        to_customer(db, booking, service, "service_booking_pending_approval", "Booking received", f"Your booking for {name} on {_when(booking, service)} is waiting for approval.")
        to_staff(db, booking, service, "service_booking_new", "New service booking", f"A booking for {name} on {_when(booking, service)} needs your approval.",
                 access_token=access_token)


def booking_paid(db: Session, booking, service, access_token: str | None = None) -> None:
    if booking.status == "confirmed":
        booking_created(db, booking, service, access_token)
    else:
        to_customer(db, booking, service, "service_booking_pending_approval", "Booking received", f"Your booking for {service.service_name} is paid and waiting for approval.")
        to_staff(db, booking, service, "service_booking_new", "New service booking", f"A paid booking for {service.service_name} on {_when(booking, service)} needs your approval.",
                 access_token=access_token)


def booking_confirmed(db: Session, booking, service) -> None:
    to_customer(db, booking, service, "service_booking_confirmed", "Booking confirmed", f"Your booking for {service.service_name} is confirmed for {_when(booking, service)}.")


def booking_cancelled(db: Session, booking, service, *, by_customer: bool, access_token: str | None = None) -> None:
    to_customer(db, booking, service, "service_booking_cancelled", "Booking cancelled", f"Your booking for {service.service_name} on {_when(booking, service)} was cancelled.",
                reason=booking.cancel_reason)
    if by_customer:
        to_staff(db, booking, service, "service_booking_cancelled", "Booking cancelled", f"A customer cancelled the booking for {service.service_name} on {_when(booking, service)}.",
                 access_token=access_token)


def cancellation_requested(db: Session, booking, service, access_token: str | None = None) -> None:
    to_staff(db, booking, service, "service_booking_cancellation_requested", "Cancellation requested",
             f"A customer asked to cancel the booking for {service.service_name} on {_when(booking, service)}.", reason=booking.cancel_reason, access_token=access_token)


def cancellation_rejected(db: Session, booking, service) -> None:
    to_customer(db, booking, service, "service_booking_cancellation_rejected", "Cancellation not approved",
                f"Your request to cancel the booking for {service.service_name} was not approved.", reason=booking.decision_reason)


def completed(db: Session, booking, service) -> None:
    to_customer(db, booking, service, "service_booking_completed", "Booking completed", f"Thank you for visiting {service.service_name}.")


def no_show(db: Session, booking, service) -> None:
    to_customer(db, booking, service, "service_booking_no_show", "Booking marked as missed", f"You did not attend your booking for {service.service_name} on {_when(booking, service)}.")


# ---- scheduler: reminders and unpaid holds ----------------------------------------------------------------------------------------------

def reminder_hours() -> list[int]:
    hours = {int(p) for p in str(settings.SERVICE_BOOKING_REMINDER_HOURS or "").replace(" ", "").split(",") if p.isdigit() and int(p) > 0}
    return sorted(hours, reverse=True)


def due_step(remaining: timedelta, hours: list[int]) -> int | None:
    """The smallest reminder step still ahead (24 h out -> 24, 40 min out -> 1), None when it has started or is further away than every step."""
    if remaining <= timedelta(0):
        return None
    for step in sorted(hours):
        if remaining <= timedelta(hours=step):
            return step
    return None


def send_due_reminders(db: Session, now: datetime | None = None) -> int:
    """One scheduler tick: a reminder per confirmed booking per step, claimed first so it goes out once however many workers run."""
    from booking_service.models.service_booking_model import ServiceBooking

    hours = reminder_hours()
    if not hours:
        return 0
    now = now or slots.now_utc()
    sent = 0
    rows = db.query(ServiceBooking).filter(ServiceBooking.status == "confirmed", ServiceBooking.starts_at > now,
                                           ServiceBooking.starts_at <= now + timedelta(hours=max(hours))).all()
    for booking in rows:
        step = due_step(booking.starts_at - now, hours)
        if step is None or not booking.customer_user_id:
            continue
        service = booking.service
        data = payload(booking, service, "service_booking_reminder", kind=f"{step}h")
        if _deliver(db, users=[booking.customer_user_id], category="service_booking_reminder", title="Booking reminder",
                    body=f"{service.service_name} starts at {_when(booking, service)}.", data=data, tenant_id=booking.tenant_id,
                    key=f"service_booking_reminder:{booking.id}:{step}h:{booking.starts_at.isoformat()}"):
            sent += 1
    return sent


def _loop(interval: int) -> None:
    from booking_service.db.database import SessionLocal
    from booking_service.services import service_booking_service as bookings

    while True:
        try:
            with SessionLocal() as db:
                bookings.expire_stale_holds(db)
                count = send_due_reminders(db)
            if count:
                logger.info("service booking reminders sent: %s", count)
        except Exception:
            logger.exception("service booking tick failed")
        time.sleep(interval)


def start_scheduler() -> threading.Thread | None:
    """Started from app startup, like the Training reminders (no Celery beat here); every worker may run it, reminders are claimed."""
    if not settings.service_booking_reminders_enabled:
        return None
    thread = threading.Thread(target=_loop, args=(settings.SERVICE_BOOKING_REMINDER_INTERVAL_SECONDS,), name="service-booking-reminders", daemon=True)
    thread.start()
    logger.info("Service booking scheduler started (every %ss)", settings.SERVICE_BOOKING_REMINDER_INTERVAL_SECONDS)
    return thread
