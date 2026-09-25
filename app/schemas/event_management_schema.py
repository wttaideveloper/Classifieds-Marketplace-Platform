"""Response schemas for Event attendee management and the Event dashboard (Phase 2.3)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common_schema import EventType, PaginatedResponse
from app.schemas.event_schema import EventModules
from app.schemas.event_session_attendance_schema import EventSessionAttendanceState, EventSessionAttendanceSummary

# What an attendee's payment looks like. Derived from the paired EventOrder (see app/utils/event_payments.py);
# never stored on the registration.
AttendeePaymentStatus = Literal[
    "free", "unpaid", "pending", "paid", "refund_requested", "refunded", "cancelled", "failed"
]
# EventRegistration.status values the backend writes today.
RegistrationStatusFilter = Literal["confirmed", "attended", "cancelled", "no_show"]
AttendeeSort = Literal["newest", "oldest", "name", "email"]


# --------------------------------------------------------------------------- attendees


class EventAttendeeAnswer(BaseModel):
    """One answer from the registration form, with its question label when the form still defines it."""

    field_id: str = Field(..., description="Registration form field id (the key the answer is stored under).")
    label: str = Field(..., description="The question label; falls back to field_id when the form no longer defines it.")
    value: Any = Field(None, description="The submitted answer (string, boolean or list of strings).")


class EventAttendeeResponse(BaseModel):
    registration_id: UUID
    event_id: UUID
    participant_name: str
    participant_email: str
    registration_status: str | None = Field(None, description="confirmed | attended | cancelled | no_show (null only for a legacy row with no status)")
    registration_reference: str | None = Field(None, description="The registration's reference code (also the QR code value).")
    ticket_type_id: str | None = None
    ticket_type_name: str | None = Field(None, description="Resolved from the event's ticket types; null if the type no longer exists.")
    quantity: int = Field(1, description="Tickets on the paired order (1 when there is no order).")
    payment_status: AttendeePaymentStatus = Field(
        ...,
        description="Derived from the paired order: paid, pending, refund_requested, refunded, cancelled, failed. "
                    "With no order: 'free' for a free event, 'unpaid' for a paid one.",
    )
    order_id: UUID | None = Field(None, description="The paired EventOrder, when there is one.")
    order_status: str | None = Field(None, description="Raw status of the paired order.")
    amount: float | None = Field(None, description="Total of the paired order (unit price x quantity), if parseable.")
    currency: str | None = None
    is_checked_in: bool = Field(..., description="True when the registration status is 'attended'.")
    checked_in_at: datetime | None = None
    checked_out_at: datetime | None = None
    registered_at: datetime = Field(..., description="When the registration was created (UTC).")
    custom_answers: list[EventAttendeeAnswer] = Field(default_factory=list)
    session_attendance: list[EventSessionAttendanceState] = Field(
        default_factory=list,
        description="One entry per session of the event: checked in or not. Separate from (and never changes) is_checked_in. "
                    "Empty for an event with no sessions, and in the CSV export.",
    )


class EventAttendeePaginatedResponse(PaginatedResponse[EventAttendeeResponse]):
    pass


# --------------------------------------------------------------------------- dashboard


class DashboardEvent(BaseModel):
    id: UUID
    title: str | None = None
    status: str = Field(..., description="Workflow status, unchanged.")
    lifecycle_state: Literal["upcoming", "ongoing", "finished"] | None = Field(
        None, description="Time-based state from the existing lifecycle_state logic."
    )
    event_type: EventType = Field(..., description="Legacy events resolve to 'other'.")
    modules: EventModules = Field(..., description="Effective module configuration (informational; not enforced here).")
    pricing_type: str | None = None
    currency: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    time_zone: str | None = None


class DashboardRegistrations(BaseModel):
    total: int = Field(..., description="Every registration, whatever its status.")
    active: int = Field(..., description="confirmed + attended: registrations that hold a seat.")
    confirmed: int
    attended: int
    cancelled: int
    no_show: int
    other: int = Field(..., description="Registrations with any status not listed above.")


class DashboardCapacity(BaseModel):
    capacity: int | None = Field(None, description="Configured capacity; null when the event has none (unlimited).")
    unlimited: bool
    seats_taken: int = Field(..., description="Active registrations plus the extra seats of multi-quantity confirmed orders; each seat once.")
    seats_reserved: int = Field(..., description="Seats held by live (unexpired) waitlist payment offers.")
    available_seats: int | None = Field(None, description="capacity - seats_taken - seats_reserved, floored at 0; null when unlimited.")
    is_full: bool | None = Field(None, description="True when no seat is available; null when unlimited.")
    fill_percentage: float | None = Field(None, description="seats_taken / capacity * 100 (1 decimal); null when unlimited or capacity is 0.")


class DashboardAttendance(BaseModel):
    checked_in: int = Field(..., description="Registrations with status 'attended'.")
    not_checked_in: int = Field(..., description="Confirmed registrations that have not checked in.")
    attendance_percentage: float | None = Field(None, description="checked_in / (confirmed + attended) * 100 (1 decimal); null when there are no active registrations.")


class DashboardWaitlist(BaseModel):
    total: int
    waiting: int
    payment_pending: int
    promoted: int
    expired: int
    left: int
    other: int


class DashboardOrders(BaseModel):
    total: int
    successful: int = Field(..., description="Orders that are paid (status confirmed/completed with confirmed payment).")
    pending: int
    refund_requested: int
    refunded: int
    cancelled: int
    failed: int


class RevenueLine(BaseModel):
    currency: str | None = None
    total_revenue: float
    refunded_amount: float
    pending_refund_amount: float
    paid_orders: int


class DashboardRevenue(BaseModel):
    """Revenue from EventOrder rows only (never from ticket prices or registration counts).

    total_revenue = sum of the totals of PAID orders. Refunded, refund-requested and cancelled orders are not
    in it; refunded / pending-refund totals are reported beside it. The scalar fields are null when the
    event's orders span more than one currency (mixed_currency=true) — use by_currency then.
    """

    currency: str | None = None
    total_revenue: float | None = None
    refunded_amount: float | None = None
    pending_refund_amount: float | None = None
    paid_orders: int = 0
    mixed_currency: bool = False
    by_currency: list[RevenueLine] = Field(default_factory=list)
    unparseable_orders: int = Field(0, description="Orders whose amount could not be read; they are excluded from every total.")


class EventDashboardResponse(BaseModel):
    event: DashboardEvent
    registrations: DashboardRegistrations
    capacity: DashboardCapacity
    attendance: DashboardAttendance
    waitlist: DashboardWaitlist
    orders: DashboardOrders
    revenue: DashboardRevenue
    sessions: list[EventSessionAttendanceSummary] = Field(
        default_factory=list, description="Per-session attendance for the event's sessions (empty when it has none)."
    )
    generated_at: datetime
