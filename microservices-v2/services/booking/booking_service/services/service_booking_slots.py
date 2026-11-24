"""Service booking slots: the weekly schedule turned into bookable times, with capacity and provider conflicts worked out.

Rules (docs/service-bookings.md):
* The Service's `availability_schedule` (day, start_time, end_time, slot_length) is written in the Service's own IANA time zone (`services.time_zone`).
  A slot starts every `slot_length` minutes and must fit before `end_time`; a booking lasts the Service's `duration` minutes.
* `max_participants` is the capacity of ONE time slot (the bookings that start at the same time); a service without it holds one booking per slot.
* A booking holds its place while it is pending, confirmed, in progress or has a cancellation requested. An unpaid booking holds it only until its hold
  runs out. Cancelled, completed, no-show and expired bookings hold nothing.
* A provider cannot be in two places: another booking of the same provider that overlaps in time, for any service, makes the slot unavailable, except a
  booking of the SAME service starting at the same time (a group session shares its provider).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from mskit.settings import settings
from booking_service.models.service_booking_model import ServiceBooking

ACTIVE_STATUSES = ("pending", "confirmed", "in_progress", "cancellation_requested")
DEFAULT_TIME_ZONE = "Asia/Kolkata"
DEFAULT_RULES = {"free_cancellation_hours": 24, "refund_percent_before": 100, "refund_percent_after": 0, "allow_request_after_window": True}
DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
BOOKABLE_SERVICE_STATUS = "active"


def now_utc() -> datetime:
    """Naive UTC, the way bookings are stored."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def tz_of(service) -> ZoneInfo:
    try:
        return ZoneInfo(getattr(service, "time_zone", None) or DEFAULT_TIME_ZONE)
    except Exception:
        return ZoneInfo(DEFAULT_TIME_ZONE)


def rules_of(service) -> dict:
    stored = getattr(service, "cancellation_rules", None) or {}
    return {**DEFAULT_RULES, **{k: v for k, v in stored.items() if k in DEFAULT_RULES and v is not None}}


def capacity_of(service) -> int:
    return max(int(getattr(service, "max_participants", None) or 1), 1)


def duration_of(service) -> int:
    return max(int(getattr(service, "duration", None) or 0), 1)


def delivery_modes_of(service) -> list[str]:
    """in_person / virtual / hybrid. A service that never set them is worked out from its free-text delivery format."""
    modes = [m for m in (getattr(service, "delivery_modes", None) or []) if m in ("in_person", "virtual", "hybrid")]
    if modes:
        return modes
    text = (getattr(service, "delivery_format", None) or "").lower()
    if "hybrid" in text:
        return ["hybrid"]
    if "virtual" in text or "online" in text:
        return ["virtual"]
    return ["in_person"]


def bookable_reason(service) -> str | None:
    """Why customers cannot book this service right now, or None."""
    if service is None or getattr(service, "is_deleted", False):
        return "This service is not available"
    if (service.status or "") != BOOKABLE_SERVICE_STATUS or service.service_status is False or service.availability_status is False:
        return "This service is not open for booking"
    if getattr(service, "booking_enabled", True) is False:
        return "Booking is switched off for this service"
    return None


def to_local_iso(value_utc: datetime | None, tz: ZoneInfo) -> str | None:
    """A stored UTC time as ISO-8601 with the service's UTC offset ("2026-11-20T09:00:00+05:30")."""
    if value_utc is None:
        return None
    return value_utc.replace(tzinfo=timezone.utc).astimezone(tz).isoformat()


def parse_aware(value) -> datetime:
    """An ISO-8601 time that carries a UTC offset (or Z) as naive UTC. Raises ValueError for anything else."""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("starts_at must include a UTC offset, for example 2026-11-20T09:00:00+05:30")
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _hhmm(value: str | None) -> time | None:
    try:
        hours, minutes = str(value).split(":")[:2]
        return time(int(hours), int(minutes))
    except (ValueError, TypeError):
        return None


def day_slots(service, day: date) -> list[tuple[datetime, datetime]]:
    """The slot starts and ends of one local calendar day, as aware datetimes in the service's zone."""
    tz = tz_of(service)
    duration = timedelta(minutes=duration_of(service))
    out: list[tuple[datetime, datetime]] = []
    for entry in service.availability_schedule or []:
        if not isinstance(entry, dict) or str(entry.get("day", "")).strip().lower() != DAY_NAMES[day.weekday()] or entry.get("is_available") is False:
            continue
        start, end = _hhmm(entry.get("start_time")), _hhmm(entry.get("end_time"))
        try:
            step = timedelta(minutes=max(int(str(entry.get("slot_length") or 0)), 0)) or duration
        except ValueError:
            step = duration
        if start is None or end is None or end <= start:
            continue
        cursor, limit = datetime.combine(day, start, tzinfo=tz), datetime.combine(day, end, tzinfo=tz)
        while cursor + duration <= limit:
            out.append((cursor, cursor + duration))
            cursor += step
    return out


def holding_clause(now: datetime):
    """SQL: the bookings that hold a place right now."""
    return and_(
        ServiceBooking.status.in_(ACTIVE_STATUSES),
        or_(ServiceBooking.hold_expires_at.is_(None), ServiceBooking.hold_expires_at > now, ServiceBooking.status != "pending"),
    )


@dataclass
class Slot:
    start: datetime          # aware, service zone
    end: datetime
    capacity: int
    available_seats: int
    reason: str | None       # None | full | provider_busy | too_soon | too_far

    @property
    def available(self) -> bool:
        return self.reason is None


def slots_between(db: Session, service, first: date, last: date, now: datetime | None = None) -> list[Slot]:
    """Every slot of the local days first..last (inclusive) with its remaining seats and, when it cannot be booked, why."""
    now = now or now_utc()
    tz = tz_of(service)
    capacity = capacity_of(service)
    earliest = now + timedelta(minutes=settings.SERVICE_BOOKING_MIN_NOTICE_MINUTES)
    latest = now + timedelta(days=settings.SERVICE_BOOKING_MAX_ADVANCE_DAYS)
    slots = [(s, e) for n in range((last - first).days + 1) for s, e in day_slots(service, first + timedelta(days=n))]
    if not slots:
        return []
    low = min(s for s, _ in slots).astimezone(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    high = max(e for _, e in slots).astimezone(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
    who = [ServiceBooking.service_id == service.id]
    if service.provider_user_id:
        who.append(ServiceBooking.provider_user_id == service.provider_user_id)
    held = db.query(ServiceBooking).filter(holding_clause(now), or_(*who), ServiceBooking.starts_at < high, ServiceBooking.ends_at > low).all()

    out: list[Slot] = []
    for start, end in slots:
        s_utc, e_utc = start.astimezone(timezone.utc).replace(tzinfo=None), end.astimezone(timezone.utc).replace(tzinfo=None)
        seats = sum(b.participants or 1 for b in held if b.service_id == service.id and b.starts_at == s_utc)
        busy = bool(service.provider_user_id) and any(
            b.provider_user_id == service.provider_user_id and b.starts_at < e_utc and b.ends_at > s_utc and not (b.service_id == service.id and b.starts_at == s_utc)
            for b in held
        )
        reason = "too_soon" if s_utc < earliest else "too_far" if s_utc > latest else "provider_busy" if busy else "full" if seats >= capacity else None
        out.append(Slot(start=start, end=end, capacity=capacity, available_seats=max(capacity - seats, 0), reason=reason))
    return out


def find_slot(db: Session, service, starts_at_utc: datetime, now: datetime | None = None) -> Slot | None:
    """The slot that starts exactly at `starts_at_utc`, or None when the schedule does not offer one."""
    local_day = starts_at_utc.replace(tzinfo=timezone.utc).astimezone(tz_of(service)).date()
    for slot in slots_between(db, service, local_day, local_day, now):
        if slot.start.astimezone(timezone.utc).replace(tzinfo=None) == starts_at_utc:
            return slot
    return None
