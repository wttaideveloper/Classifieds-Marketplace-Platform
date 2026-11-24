"""Service booking request / response shapes. The contract is explained in docs/service-bookings.md."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# copied from app/schemas/service_schema.py (catalog): the booking contract uses the same shapes
DeliveryMode = Literal["in_person", "virtual", "hybrid"]


class CancellationRules(BaseModel):
    """The structured cancellation policy a booking follows (the free-text `cancellation_policy` stays as the wording shown to people)."""

    free_cancellation_hours: int = Field(24, ge=0, le=24 * 90, description="A customer can cancel on their own up to this many hours before the start.")
    refund_percent_before: int = Field(100, ge=0, le=100, description="Share refunded when cancelled inside the free window.")
    refund_percent_after: int = Field(0, ge=0, le=100, description="Share refunded when an admin approves a cancellation requested after the window.")
    allow_request_after_window: bool = Field(True, description="After the window a customer can still ask (an admin decides); false = not allowed.")



BookingStatus = Literal["pending", "confirmed", "in_progress", "completed", "cancellation_requested", "cancelled", "no_show", "expired"]
PaymentStatus = Literal["not_required", "unpaid", "paid", "refunded", "partially_refunded"]


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
    status: BookingStatus
    payment_status: PaymentStatus
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
    can_reschedule: bool = Field(False, description="Always false in this release (rescheduling is a later phase).")
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
    reason: Literal["full", "provider_busy", "too_soon", "too_far"] | None = None


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
    requires_approval: bool
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
    duration_minutes: int
    provider_user_id: UUID | None = None
    provider_name: str | None = None
