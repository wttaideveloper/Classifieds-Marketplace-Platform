"""Service bookings API. Contract and rules: docs/service-bookings.md.

    /services/{id}/available-slots              anyone: the bookable times of a service
    /services/{id}/booking-settings             staff: the booking settings of a service, including the meeting link
    /service-bookings                           book; one booking; cancel; status; cancellation decisions; payment status; history
    /users/me/service-bookings                  my bookings
    /enterprises/{id}/service-bookings          the enterprise's bookings (Enterprise Admin; a Provider sees their own)
"""
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.core.catalog_access import get_catalog_access
from app.core.dependencies import extract_access_token, get_current_user
from app.db.database import get_db
from app.models.service_model import Service
from app.schemas.service_booking_schema import (
    AssignedBookingList, AvailabilityChange, AvailabilityImpact, BookingCreate, BookingDetail, BookingListResponse, BookingSettings, BookingSummary, CancelBody, PlatformSummary, ServiceCountList, DecisionBody, HistoryItem, PaymentBody, SlotsResponse, StatusBody,
)
from app.services import service_booking_service as svc
from app.services import service_booking_slots as slots

router = APIRouter(tags=["Service Bookings"])
me_router = APIRouter(tags=["Service Bookings"])
enterprise_router = APIRouter(tags=["Service Bookings"])
service_router = APIRouter(tags=["Service Bookings"])

MAX_SLOT_DAYS = 31


def _actor(request: Request, db: Session, user: dict) -> svc.Actor:
    return svc.actor_from(db, user, extract_access_token(request))


# ---------------------------------------------------------------- slots and settings (under /services)

@service_router.get(
    "/{service_id}/available-slots", response_model=SlotsResponse, summary="The bookable times of a service",
    description=(
        "Public. Give `date` (one day) or `from` and `to` (up to 31 days), as calendar dates in the service's own time zone (`time_zone` in the answer). "
        "Each slot has `starts_at` / `ends_at` as ISO-8601 with the service's UTC offset: send `starts_at` back unchanged to book it. A slot says how many "
        "seats are left and, when it cannot be booked, why (`full`, `provider_busy`, `too_soon`, `too_far`). Slots already count the bookings that hold a place "
        "(pending, confirmed, in progress, cancellation requested; an unpaid booking only until its hold ends) and the provider's other bookings. "
        "`timezone` (optional, IANA) adds `starts_at_display` in that zone. `provider_id` and `delivery_mode` only check that the service offers them."
    ),
)
def available_slots(
    service_id: UUID = Path(...), date_: date | None = Query(None, alias="date"), from_: date | None = Query(None, alias="from"), to: date | None = Query(None),
    provider_id: UUID | None = Query(None), delivery_mode: str | None = Query(None, pattern="^(in_person|virtual|hybrid)$"),
    timezone: str | None = Query(None, description="IANA zone for starts_at_display"), db: Session = Depends(get_db),
):
    from fastapi import HTTPException

    service = db.query(Service).filter(Service.id == service_id, Service.is_deleted.is_(False)).first()
    if service is None or (service.status or "") != slots.BOOKABLE_SERVICE_STATUS:
        raise svc._err(404, "SERVICE_NOT_FOUND", "Service not found")
    first = date_ or from_ or datetime.now(slots.tz_of(service)).date()          # today, in the service's own time zone
    last = date_ or to or (first + timedelta(days=6))
    if last < first:
        raise svc._err(422, "INVALID_RANGE", "`to` must be on or after `from`")
    if (last - first).days >= MAX_SLOT_DAYS:
        raise svc._err(422, "RANGE_TOO_LONG", f"Ask for at most {MAX_SLOT_DAYS} days at a time")
    display = None
    if timezone:
        try:
            display = ZoneInfo(timezone)
        except Exception:
            raise HTTPException(status_code=422, detail={"message": "timezone must be an IANA name such as Asia/Kolkata", "code": "INVALID_TIMEZONE"})
    modes = slots.delivery_modes_of(service)
    problem = slots.bookable_reason(service)
    if provider_id is not None and provider_id != service.provider_user_id:
        problem = "That provider does not offer this service"
    if delivery_mode and delivery_mode not in modes:
        problem = "This service is not offered that way"
    days: dict[str, list] = {}
    if not problem:
        for s in slots.slots_between(db, service, first, last):
            days.setdefault(s.start.date().isoformat(), []).append({
                "starts_at": s.start.isoformat(), "ends_at": s.end.isoformat(), "starts_at_display": s.start.astimezone(display).isoformat() if display else None,
                "available": s.available, "available_seats": s.available_seats, "capacity": s.capacity, "reason": s.reason,
            })
    return {
        "service_id": service.id, "service_name": service.service_name, "time_zone": str(slots.tz_of(service)), "duration_minutes": slots.duration_of(service),
        "capacity": slots.capacity_of(service), "delivery_modes": modes, "provider_id": service.provider_user_id, "provider_name": service.provider_name or service.instructor_name,
        "bookable": problem is None, "reason": problem, "hold_minutes": svc.settings.SERVICE_BOOKING_HOLD_MINUTES,
        "days": [{"date": (first + timedelta(days=n)).isoformat(), "slots": days.get((first + timedelta(days=n)).isoformat(), [])} for n in range((last - first).days + 1)],
    }


@service_router.post(
    "/{service_id}/availability-impact", response_model=AvailabilityImpact, summary="Which existing bookings a planned availability change touches (Enterprise Admin)",
    description=(
        "Send the planned `availability`, `blocked_periods`, `max_participants` and/or `duration_minutes` (only what changes). The answer lists the upcoming bookings "
        "(confirmed, in progress, with a cancellation request, or holding a slot for payment) that would fall in a blocked period (`blocked`), outside the new schedule "
        "(`outside_schedule`) or above the new capacity (`over_capacity`). Nothing is saved: show it as the warning, then save with PATCH /services/{id}. Saving never cancels "
        "or moves these bookings (lowering capacity below them is refused with 409 CAPACITY_BELOW_BOOKINGS)."
    ),
)
def availability_impact(request: Request, service_id: UUID, payload: AvailabilityChange, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    changes = {}
    if payload.availability is not None:
        changes["availability_schedule"] = [e.model_dump() for e in payload.availability]
    if payload.blocked_periods is not None:
        changes["blocked_periods"] = [p.model_dump(mode="json") for p in payload.blocked_periods]
    if payload.max_participants is not None:
        changes["max_participants"] = payload.max_participants
    if payload.duration_minutes is not None:
        changes["duration_minutes"] = payload.duration_minutes
    return svc.availability_impact(db, _actor(request, db, user), service_id, changes)


@service_router.get(
    "/{service_id}/booking-settings", response_model=BookingSettings, summary="A service's booking settings, with the meeting link (staff)",
    description="Enterprise Admin of the owning tenant, the service's Provider, or a Super Admin. Everything the service form sets for booking, including the meeting link and passcode that the public catalog never shows.",
)
def booking_settings(service_id: UUID = Path(...), db: Session = Depends(get_db), access=Depends(get_catalog_access)):
    from app.repository.service_repo import get_service_by_id

    if access.role not in ("admin", "provider", "super_admin"):
        raise svc._err(403, "NOT_ALLOWED", "Staff only")
    service = get_service_by_id(db, service_id, access=access)
    if service is None:
        raise svc._err(404, "SERVICE_NOT_FOUND", "Service not found")
    return {
        "service_id": service.id, "booking_enabled": service.booking_enabled is not False, "requires_approval": bool(service.requires_approval),
        "time_zone": str(slots.tz_of(service)), "delivery_modes": slots.delivery_modes_of(service), "cancellation_rules": slots.rules_of(service),
        "cancellation_policy": service.cancellation_policy, "venue_instructions": service.venue_instructions, "meeting_url": service.meeting_url,
        "meeting_provider": service.meeting_provider, "meeting_passcode": service.meeting_passcode, "meeting_instructions": service.meeting_instructions,
        "max_participants": service.max_participants, "duration_minutes": slots.duration_of(service), "provider_user_id": service.provider_user_id,
        "min_notice_minutes": service.min_notice_minutes, "max_advance_days": service.max_advance_days, "blocked_periods": service.blocked_periods,
        "provider_name": service.provider_name or service.instructor_name,
    }


# ---------------------------------------------------------------- bookings

@router.post(
    "", response_model=BookingDetail, status_code=status.HTTP_201_CREATED, summary="Book a service",
    description=(
        "A customer books for themselves; the Enterprise Admin can book for a customer (send `customer_name` or `customer_user_id`; `paid: true` confirms a paid "
        "booking at once). Free service: confirmed straight away, or `pending` when the service requires approval. Paid service: `pending` with "
        "`payment_status: unpaid` and the slot held for the hold time (`hold_expires_at`); it is confirmed once paid (and approved, if required). "
        "Send an `Idempotency-Key` header: repeating the request with the same key returns the first booking (200) instead of booking twice. "
        "Errors (`detail.code`): SLOT_FULL, PROVIDER_UNAVAILABLE, SLOT_NOT_OFFERED, SLOT_NOT_BOOKABLE, DUPLICATE_BOOKING, SERVICE_NOT_BOOKABLE, DELIVERY_MODE_REQUIRED, INVALID_STARTS_AT."
    ),
)
def create_booking(
    request: Request, response: Response, payload: BookingCreate, idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    db: Session = Depends(get_db), user: dict = Depends(get_current_user),
):
    actor = _actor(request, db, user)
    booking, created = svc.create_booking(db, actor, payload, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
    return svc.booking_view(db, booking, actor)


STAGES = "^(pending_payment|legacy_pending_approval|confirmed|in_progress|completed|cancellation_requested|cancelled|no_show|expired)$"


@router.get(
    "/assigned", response_model=AssignedBookingList, summary="My assigned appointments (Provider)",
    description=(
        "Only the bookings assigned to the signed-in Provider in their own tenant, across its enterprises. Each item has the list fields plus `duration_minutes`, "
        "`venue` or `meeting` (the assigned Provider always sees the link) and `allowed_statuses`. Filters: `status` (comma separated), `stage`, `service_id`, `q` "
        "(customer, phone, reference, service), `date_from` / `date_to` (start date, UTC), `when` = upcoming | past | all, `delivery_mode`. Anyone who is not a Provider: 403 PROVIDER_ONLY."
    ),
)
def assigned_bookings(
    request: Request, status_filter: str | None = Query(None, alias="status"), stage: str | None = Query(None, pattern=STAGES), service_id: UUID | None = None,
    q: str | None = Query(None, max_length=200), date_from: date | None = None, date_to: date | None = None, when: str | None = Query(None, pattern="^(upcoming|past|all)$"),
    delivery_mode: str | None = Query(None, pattern="^(in_person|virtual|hybrid)$"), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db), user: dict = Depends(get_current_user),
):
    return svc.list_assigned(db, _actor(request, db, user), page=page, page_size=page_size, when=when, status=status_filter, stage=stage, service_id=service_id, q=q,
                             date_from=date_from, date_to=date_to, delivery_mode=delivery_mode)


@router.get(
    "/admin", response_model=BookingListResponse, summary="Service bookings across all enterprises (Super Admin, read only)",
    description=(
        "Every enterprise's service bookings, server-side paged with a reliable `total`. Filters: `enterprise_id`, `service_id`, `provider_id`, `status` (comma separated), "
        "`stage`, `payment_status`, `refund_status`, `source` = customer | manual, `delivery_mode`, `q` (customer, phone, reference, service, provider, enterprise), "
        "`date_from` / `date_to` (start date, UTC), `when` = upcoming | past | all. Anyone else: 403 SUPER_ADMIN_ONLY."
    ),
)
def platform_bookings(
    request: Request, enterprise_id: UUID | None = None, service_id: UUID | None = None, provider_id: UUID | None = None,
    status_filter: str | None = Query(None, alias="status"), stage: str | None = Query(None, pattern=STAGES),
    payment_status: str | None = Query(None, pattern="^(not_required|unpaid|paid|refunded|partially_refunded)$"),
    refund_status: str | None = Query(None, pattern="^(pending|processing|completed|failed)$"), source: str | None = Query(None, pattern="^(customer|manual)$"),
    delivery_mode: str | None = Query(None, pattern="^(in_person|virtual|hybrid)$"), q: str | None = Query(None, max_length=200),
    date_from: date | None = None, date_to: date | None = None, when: str | None = Query(None, pattern="^(upcoming|past|all)$"),
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db), user: dict = Depends(get_current_user),
):
    return svc.list_platform(db, _actor(request, db, user), page=page, page_size=page_size, when=when, enterprise_id=enterprise_id, service_id=service_id,
                             provider_id=provider_id, status=status_filter, stage=stage, payment_status=payment_status, refund_status=refund_status, source=source,
                             delivery_mode=delivery_mode, q=q, date_from=date_from, date_to=date_to)


@router.get("/admin/summary", response_model=PlatformSummary, summary="Booking totals across all enterprises (Super Admin)",
            description="Counted in the database across every enterprise (optionally one `enterprise_id`, and a start-date range): the total, per stage, per refund status, and exceptions.")
def platform_summary(request: Request, enterprise_id: UUID | None = None, date_from: date | None = None, date_to: date | None = None,
                     db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.platform_summary(db, _actor(request, db, user), enterprise_id=enterprise_id, date_from=date_from, date_to=date_to)


@router.get("/admin/service-counts", response_model=ServiceCountList, summary="Booking counts per service (Super Admin)",
            description="For the Bookings column: send the ids of the services on the page (`service_ids`, comma separated, at most 200). Each gets its total and counts per stage; a service with no bookings gets 0.")
def platform_service_counts(request: Request, service_ids: str = Query(..., max_length=8000), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    try:
        ids = list(dict.fromkeys(UUID(x.strip()) for x in service_ids.split(",") if x.strip()))
    except ValueError:
        raise svc._err(422, "INVALID_SERVICE_IDS", "service_ids must be comma separated UUIDs")
    if len(ids) > 200:
        raise svc._err(422, "TOO_MANY_SERVICE_IDS", "At most 200 service ids")
    return svc.service_counts(db, _actor(request, db, user), ids)


@router.get("/{booking_id}", response_model=BookingDetail, summary="One booking",
            description="The customer's own booking, or any booking of the Enterprise Admin's enterprise (a Provider: their own). Anyone else gets 404. Times are ISO-8601 with the service's UTC offset; `timezone` is the IANA name. "
                        "`meeting.url` appears for a customer from `meeting.joins_at` until shortly after the end; staff always see it.")
def get_booking(request: Request, booking_id: UUID, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    actor = _actor(request, db, user)
    svc.expire_stale_holds(db)
    return svc.booking_view(db, svc.get_for(db, actor, booking_id), actor)


@router.post(
    "/{booking_id}/cancel", response_model=BookingDetail, summary="Cancel a booking, or ask to",
    description=(
        "Customer: inside the free window (or while the booking is still pending) the booking is cancelled at once and capacity is released; later it becomes "
        "`cancellation_requested` (a `reason` of at least 3 characters is needed) for the Enterprise Admin to decide, or 409 `CANCEL_NOT_ALLOWED` with the policy when the "
        "service does not allow requests. The booking detail's `can_cancel`, `cancel_mode` and `cancel_blocked_reason` say which applies. A paid booking records the refund "
        "(`refund_percent`, `refund_amount`, `payment_status`): no money moves yet. The Enterprise Admin cancelling uses the same call (reason required, default refund 100%)."
    ),
)
def cancel_booking(request: Request, booking_id: UUID, payload: CancelBody | None = None, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    actor = _actor(request, db, user)
    body = payload or CancelBody()
    return svc.booking_view(db, svc.cancel_booking(db, actor, booking_id, body.reason, body.refund_percent), actor)


@router.patch(
    "/{booking_id}/status", response_model=BookingDetail, summary="Change a booking's status (staff)",
    description=(
        "pending -> confirmed | cancelled; confirmed -> in_progress | completed | no_show | cancelled; in_progress -> completed. A paid service cannot be confirmed before the "
        "payment is recorded (409 PAYMENT_REQUIRED); in_progress opens 15 minutes before the start; completed and no_show only once it has started; cancelled needs a reason. "
        "A Provider can only start, complete or mark no-show their own bookings. A booking with a cancellation request is decided with approve / reject. "
        "409 INVALID_TRANSITION lists the `allowed` statuses."
    ),
)
def change_status(request: Request, booking_id: UUID, payload: StatusBody, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    actor = _actor(request, db, user)
    return svc.booking_view(db, svc.change_status(db, actor, booking_id, payload.status, payload.reason, payload.refund_percent), actor)


@router.post("/{booking_id}/cancellation/approve", response_model=BookingDetail, summary="Approve a cancellation request (Enterprise Admin)",
             description="Cancels the booking, releases the slot and records the refund: the service's `refund_percent_after` unless `refund_percent` is sent.")
def approve_cancellation(request: Request, booking_id: UUID, payload: DecisionBody | None = None, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    actor = _actor(request, db, user)
    body = payload or DecisionBody()
    return svc.booking_view(db, svc.decide_cancellation(db, actor, booking_id, True, body.reason, body.refund_percent), actor)


@router.post("/{booking_id}/cancellation/reject", response_model=BookingDetail, summary="Reject a cancellation request (Enterprise Admin)",
             description="The booking goes back to the status it had; a `reason` (at least 3 characters) is required and the customer is told.")
def reject_cancellation(request: Request, booking_id: UUID, payload: DecisionBody, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    actor = _actor(request, db, user)
    return svc.booking_view(db, svc.decide_cancellation(db, actor, booking_id, False, payload.reason), actor)


@router.post("/{booking_id}/payment", response_model=BookingDetail, summary="Record that a booking was paid (Enterprise Admin)",
             description="Sets `payment_status` to `paid` (status only: no payment gateway is involved). A pending booking is confirmed unless the service requires approval. 409 HOLD_EXPIRED if the unpaid hold already ran out.")
def record_payment(request: Request, booking_id: UUID, payload: PaymentBody | None = None, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    actor = _actor(request, db, user)
    if actor.role == "customer":
        raise svc._err(403, "NOT_ALLOWED", "Only the Enterprise Admin can record a payment")
    return svc.booking_view(db, svc.mark_paid(db, actor, booking_id, (payload or PaymentBody()).reference), actor)


@router.post("/{booking_id}/pay", response_model=BookingDetail, summary="Pay for my booking (demo payment)",
             description="A stand-in until a payment gateway is connected: marks the customer's own unpaid booking paid. Available only where SERVICE_BOOKING_DEMO_PAYMENT is on (off in production by default: 403 PAYMENT_NOT_AVAILABLE).")
def pay_booking(request: Request, booking_id: UUID, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    actor = _actor(request, db, user)
    if actor.is_staff:
        raise svc._err(403, "NOT_ALLOWED", "Use POST /service-bookings/{id}/payment")
    return svc.booking_view(db, svc.mark_paid(db, actor, booking_id), actor)


@router.get("/{booking_id}/history", response_model=list[HistoryItem], summary="The booking's history",
            description="Every change in order: who (role; staff also see the name), what, from which status to which, why, and when.")
def booking_history(request: Request, booking_id: UUID, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    actor = _actor(request, db, user)
    return svc.booking_view(db, svc.get_for(db, actor, booking_id), actor)["history"]


# ---------------------------------------------------------------- lists

@me_router.get("", response_model=BookingListResponse, summary="My bookings",
               description="The caller's own bookings only. `when=upcoming` (soonest first, still active), `past` (newest first, started or finished) or all (newest first); `status` is one or several comma separated statuses.")
def my_bookings(
    request: Request, status_filter: str | None = Query(None, alias="status"), when: str | None = Query(None, pattern="^(upcoming|past|all)$"),
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db), user: dict = Depends(get_current_user),
):
    return svc.list_mine(db, _actor(request, db, user), status=status_filter, when=when, page=page, page_size=page_size)


@enterprise_router.get(
    "/{enterprise_id}/service-bookings/cancellation-requests", response_model=BookingListResponse, summary="Cancellation requests waiting for a decision",
    description="Oldest request first. Enterprise Admin of the enterprise (a Provider: their own bookings; Super Admin: any).",
)
def cancellation_requests(
    request: Request, enterprise_id: UUID, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db), user: dict = Depends(get_current_user),
):
    return svc.list_for_enterprise(db, _actor(request, db, user), enterprise_id, only_requests=True, page=page, page_size=page_size)


@enterprise_router.get(
    "/{enterprise_id}/service-bookings", response_model=BookingListResponse, summary="The enterprise's bookings",
    description=(
        "Enterprise Admin of the enterprise (a Provider sees only their own; Super Admin any). Filters: `status` (comma separated), `service_id`, `provider_id`, `payment_status`, "
        "`q` (customer name, e-mail or phone, booking reference, service, provider), `date_from` / `date_to` (the start, calendar dates in UTC), `when` = upcoming | past | all (history), "
        "`source` = customer | manual, `delivery_mode`, `stage` (pending_payment, legacy_pending_approval or any status). Another enterprise's id is a 404."
    ),
)
def enterprise_bookings(
    request: Request, enterprise_id: UUID, status_filter: str | None = Query(None, alias="status"), service_id: UUID | None = None, provider_id: UUID | None = None,
    payment_status: str | None = Query(None, pattern="^(not_required|unpaid|paid|refunded|partially_refunded)$"), q: str | None = Query(None, max_length=200),
    date_from: date | None = None, date_to: date | None = None, when: str | None = Query(None, pattern="^(upcoming|past|all)$"),
    source: str | None = Query(None, pattern="^(customer|manual)$"), delivery_mode: str | None = Query(None, pattern="^(in_person|virtual|hybrid)$"),
    refund_status: str | None = Query(None, pattern="^(pending|processing|completed|failed)$"),
    stage: str | None = Query(None, pattern="^(pending_payment|legacy_pending_approval|confirmed|in_progress|completed|cancellation_requested|cancelled|no_show|expired)$"),
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db), user: dict = Depends(get_current_user),
):
    return svc.list_for_enterprise(
        db, _actor(request, db, user), enterprise_id, status=status_filter, service_id=service_id, provider_id=provider_id, payment_status=payment_status, q=q,
        date_from=date_from, date_to=date_to, when=when, page=page, page_size=page_size, source=source, delivery_mode=delivery_mode, stage=stage,
        refund_status=refund_status,
    )


@enterprise_router.get(
    "/{enterprise_id}/service-bookings/summary", response_model=BookingSummary, summary="Booking dashboard counts",
    description="Upcoming, pending payment, cancellation requests, approval bookings left from before approval was retired, and a count per status. Same access as the list.",
)
def enterprise_booking_summary(request: Request, enterprise_id: UUID, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.summary_for_enterprise(db, _actor(request, db, user), enterprise_id)
