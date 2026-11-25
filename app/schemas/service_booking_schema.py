"""Service booking request / response shapes. The contract is explained in docs/service-bookings.md."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common_schema import AvailabilityScheduleEntry
from app.schemas.service_schema import BlockedPeriod, CancellationRules, DeliveryMode

BookingStatus = Literal["pending", "confirmed", "in_progress", "completed", "cancellation_requested", "cancelled", "no_show", "expired"]
PaymentStatus = Literal["not_required", "unpaid", "paid", "refunded", "partially_refunded"]     # refunded / partially_refunded: only rows from before refund_status existed
RefundStatus = Literal["pending", "processing", "completed", "failed"]
BookingStage = Literal["pending_payment", "legacy_pending_approval", "confirmed", "in_progress", "completed", "cancellation_requested", "cancelled", "no_show", "expired"]


# ---------------------------------------------------------------- requests

class BookingCreate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "service_id": "550e8400-e29b-41d4-a716-446655440001", "starts_at": "2026-11-20T09:00:00+05:30", "delivery_mode": "in_person", "participants": 1}})

    service_id: UUID
    starts_at: str = Field(..., description="ISO-8601 with a UTC offset (or Z), exactly the `starts_at` of a slot from available-slots. A time without an offset is a 422.")
    delivery_mode: DeliveryMode | None = Field(None, description="Required when the service offers more than one way to attend.")
    provider_id: UUID | None = Field(None, description="Optional. Must be the service's provider.")
    participants: int = Field(1, ge=1, le=100, description="Seats taken in the slot (default 1).")
    notes: str | None = Field(None, max_length=1000)
    # Enterprise staff booking for a customer (a customer sending these gets 403 unless customer_user_id is their own):
    customer_user_id: UUID | None = None
    customer_name: str | None = Field(None, max_length=255)
    customer_email: str | None = Field(None, max_length=255)
    customer_phone: str | None = Field(None, max_length=40)
    paid: bool = Field(False, description="Staff only: the customer already paid, so the booking is confirmed straight away.")


class CancelBody(BaseModel):
    reason: str | None = Field(None, max_length=500, description="Optional for an instant cancellation; at least 3 characters when the cancellation is a request.")
    refund_percent: int | None = Field(None, ge=0, le=100, description="Staff only: how much of a paid booking is refunded (default 100).")


class StatusBody(BaseModel):
    status: Literal["confirmed", "in_progress", "completed", "no_show", "cancelled"]
    reason: str | None = Field(None, max_length=500, description="Required for `cancelled`.")
    refund_percent: int | None = Field(None, ge=0, le=100, description="With `cancelled`: how much of a paid booking is refunded (default 100).")


class DecisionBody(BaseModel):
    reason: str | None = Field(None, max_length=500, description="Required to reject; optional note when approving.")
    refund_percent: int | None = Field(None, ge=0, le=100, description="When approving: overrides the service's refund_percent_after.")


class PaymentBody(BaseModel):
    reference: str | None = Field(None, max_length=120, description="A receipt or transaction note, kept in the history.")


# ---------------------------------------------------------------- responses

class CustomerInfo(BaseModel):
    user_id: UUID | None = None
    name: str | None = None
    email: str | None = None
    phone: str | None = None


class BookingListItem(BaseModel):
    id: UUID
    reference: str | None = Field(None, description='The booking reference people see and can search, e.g. "SB-1A2B3C4D5E".')
    status: BookingStatus
    stage: BookingStage = Field(..., description="What the booking is waiting for: `pending` is split into pending_payment and legacy_pending_approval (made before approval was retired); otherwise the status.")
    source: Literal["customer", "manual"] = Field(..., description="customer: booked in the app; manual: booked by the clinic for the customer.")
    payment_status: PaymentStatus
    refund_status: RefundStatus | None = Field(None, description="Independent of the booking and payment status. null: no refund owed. pending: owed, not yet processed by the payment "
                                                               "gateway. completed only after the gateway confirms it. A cancelled booking stays cancelled whatever the refund does.")
    service_id: UUID
    service_name: str
    provider_id: UUID | None = None
    provider_name: str | None = None
    enterprise_id: UUID
    enterprise_name: str | None = None
    delivery_mode: DeliveryMode
    starts_at: str = Field(..., description="ISO-8601 with the service's UTC offset.")
    ends_at: str
    timezone: str
    participants: int
    amount: float
    currency: str | None = None
    can_cancel: bool
    cancel_mode: Literal["instant", "request"] | None = Field(None, description="What cancelling does now: cancel at once, or send a request the clinic decides.")
    can_reschedule: bool = Field(False, description="Always false until rescheduling is released.")
    can_join: bool
    can_check_in: bool = Field(False, description="Always false in this release: the clinic marks attendance.")
    customer: CustomerInfo
    created_at: str | None = None


class BookingListResponse(BaseModel):
    items: list[BookingListItem]
    total: int
    page: int
    page_size: int


class HistoryItem(BaseModel):
    action: str
    from_status: str | None = None
    to_status: str | None = None
    actor_role: str | None = None
    actor_name: str | None = Field(None, description="Staff only.")
    reason: str | None = None
    at: str | None = None


class BookingDetail(BookingListItem):
    duration_minutes: int
    venue: dict | None = Field(None, description="in_person and hybrid: name, line1, line2, city, state, postal_code, country, lat, lng, phone, instructions.")
    meeting: dict | None = Field(None, description="virtual and hybrid: provider, joins_at, available, and url / passcode / instructions once the link opens (staff always).")
    cancel_blocked_reason: str | None = None
    join_blocked_reason: str | None = None
    cancellation_policy_summary: str
    cancellation_policy: str | None = None
    hold_expires_at: str | None = Field(None, description="An unpaid booking holds its slot until then.")
    cancellation: dict
    payment_id: UUID | None = Field(None, description="Always null: payment is a status on the booking for now.")
    qr_payload: str | None = None
    notes: str | None = None
    history: list[HistoryItem] = Field(default_factory=list)
    allowed_statuses: list[str] | None = Field(None, description="Staff only: the statuses this booking can be moved to.")
    created_by_role: str | None = None


class SlotItem(BaseModel):
    starts_at: str = Field(..., description="Send this exact value as `starts_at` when booking.")
    ends_at: str
    starts_at_display: str | None = Field(None, description="The same instant in the `timezone` you asked for.")
    available: bool
    available_seats: int
    capacity: int
    reason: Literal["blocked", "full", "provider_busy", "too_soon", "too_far"] | None = None


class SlotDay(BaseModel):
    date: str
    slots: list[SlotItem]


class SlotsResponse(BaseModel):
    service_id: UUID
    service_name: str
    time_zone: str
    duration_minutes: int
    capacity: int
    delivery_modes: list[DeliveryMode]
    provider_id: UUID | None = None
    provider_name: str | None = None
    bookable: bool
    reason: str | None = Field(None, description="Why the service cannot be booked at all, when `bookable` is false.")
    hold_minutes: int
    days: list[SlotDay]


class BookingSettings(BaseModel):
    """Staff only: everything the booking needs from the service, including the meeting link."""

    service_id: UUID
    booking_enabled: bool
    requires_approval: bool = Field(..., description="Retired: has no effect on new bookings.")
    time_zone: str
    delivery_modes: list[DeliveryMode]
    cancellation_rules: CancellationRules
    cancellation_policy: str | None = None
    venue_instructions: str | None = None
    meeting_url: str | None = None
    meeting_provider: str | None = None
    meeting_passcode: str | None = None
    meeting_instructions: str | None = None
    max_participants: int | None = None
    min_notice_minutes: int | None = Field(None, description="Null: the platform default.")
    max_advance_days: int | None = Field(None, description="Null: the platform default.")
    blocked_periods: list[dict] | None = None
    duration_minutes: int
    provider_user_id: UUID | None = None
    provider_name: str | None = None


class BookingSummary(BaseModel):
    upcoming: int = Field(..., description="Confirmed (or in progress, or with a cancellation request) and not started yet.")
    pending_payment: int
    change_requests: int = Field(..., description="Cancellation requests waiting for a decision.")
    legacy_pending_approval: int = Field(..., description="Bookings made before approval was retired that still wait for an admin. Confirm (PATCH status confirmed) or cancel each; nothing changes them automatically.")
    by_status: dict[str, int]


class AvailabilityChange(BaseModel):
    """A planned change, to see which existing bookings it touches before saving it. Send only what changes."""

    availability: list[AvailabilityScheduleEntry] | None = Field(None, description="The new weekly schedule.")
    blocked_periods: list[BlockedPeriod] | None = Field(None, description="The full new list of blocked dates / time ranges.")
    max_participants: int | None = Field(None, ge=1)
    duration_minutes: int | None = Field(None, ge=1)


class AffectedBooking(BaseModel):
    id: UUID
    reference: str
    status: str
    stage: str
    starts_at: str
    customer_name: str | None = None
    participants: int
    payment_status: str
    reasons: list[Literal["blocked", "outside_schedule", "over_capacity"]]


class AvailabilityImpact(BaseModel):
    affected_count: int
    bookings: list[AffectedBooking]
    note: str



class AssignedBooking(BookingListItem):
    """A Provider's appointment: the list fields plus what the session needs (venue or meeting, duration, the actions the Provider may take)."""

    duration_minutes: int
    venue: dict | None = None
    meeting: dict | None = Field(None, description="virtual and hybrid: the meeting link, passcode and instructions (the assigned Provider always sees them).")
    allowed_statuses: list[str] | None = Field(None, description="What the Provider may move it to now: in_progress, completed, no_show.")
    notes: str | None = None


class AssignedBookingList(BaseModel):
    items: list[AssignedBooking]
    total: int
    page: int
    page_size: int


class PlatformSummary(BaseModel):
    total: int
    by_stage: dict[str, int] = Field(..., description="pending_payment, legacy_pending_approval, confirmed, in_progress, completed, cancellation_requested, cancelled, no_show, expired.")
    refunds: dict[str, int] = Field(..., description="Bookings per refund_status (pending, processing, completed, failed).")
    exceptions: dict[str, int] = Field(..., description="refund_pending, refund_failed, legacy_pending_approval.")


class ServiceCount(BaseModel):
    service_id: UUID
    total: int
    by_stage: dict[str, int]


class ServiceCountList(BaseModel):
    items: list[ServiceCount]
