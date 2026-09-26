"""Request/response schemas for organizer walk-in registration (Phase 2.5).

The request deliberately uses the same field names and types as ``EventRegistrationCreate`` (participant_name,
participant_email, ticket_type_id, custom_fields) so a client can reuse its registration payload, minus the
group fields (a walk-in is one attendee) and plus the two walk-in options. Unknown fields are refused, so a
client can never set the tenant, the registration source, the status, the QR value or a payment field.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.event_management_schema import AttendeePaymentStatus, EventAttendeeResponse
from app.schemas.event_accommodation_schema import AccommodationId, reject_duplicate_accommodation_selections
from app.schemas.event_meal_schema import MAX_SELECTIONS, MealId, reject_duplicate_selections

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EventWalkInRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "participant_name": "Asha Rao",
                "participant_email": "asha@example.com",
                "ticket_type_id": "general",
                "custom_fields": {"<registration form field id>": "M"},
                "check_in": True,
                "session_id": "keynote",
            }
        },
    )

    participant_name: str = Field(..., min_length=1, max_length=255, description="Participant name")
    participant_email: str = Field(
        ..., max_length=255,
        description="Participant email. Stored trimmed and lower-cased; it is the key of the one-active-registration-per-event rule.",
    )
    ticket_type_id: str | None = Field(
        None, max_length=100,
        description="One of THIS event's ticket types. Required when a paid event has ticket types; optional otherwise (validated when sent).",
    )
    custom_fields: dict[str, Any] | None = Field(
        None,
        description="Answers to the event's registration form (GET /events/{id}/registration-form), keyed by form field id. "
                    "Validated with the existing form validator: unknown ids, wrong types and missing required fields are rejected.",
    )
    meal_selections: list[MealId] | None = Field(
        None, max_length=MAX_SELECTIONS,
        description="Meal option ids from the event's meals configuration (Phase 2.6); validated by the same rules as online "
                    "registration. Optional; only allowed when the event has meals enabled.",
    )
    accommodation_selections: list[AccommodationId] | None = Field(
        None, max_length=MAX_SELECTIONS,
        description="Accommodation option ids from the event's accommodation configuration (Phase 2.7); validated by the same "
                    "rules as online registration. Optional; only allowed when the event has accommodation enabled.",
    )
    check_in: bool = Field(
        True,
        description="Check the attendee in (event level) as part of the same transaction. Default true: the usual venue flow is "
                    "register-and-admit. Not performed while a payment is still pending; the response says so.",
    )
    session_id: str | None = Field(
        None, max_length=100,
        description="Optional: also check the attendee in to this ONE session (Phase 2.4 session attendance). "
                    "Must be one of this event's sessions. Never applied to any other session.",
    )

    @field_validator("meal_selections")
    @classmethod
    def _no_duplicate_meals(cls, values):
        return reject_duplicate_selections(values)

    @field_validator("accommodation_selections")
    @classmethod
    def _no_duplicate_accommodation(cls, values):
        return reject_duplicate_accommodation_selections(values)

    @field_validator("participant_name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("participant_name must not be blank")
        return value

    @field_validator("participant_email")
    @classmethod
    def _email_shape(cls, value: str) -> str:
        value = value.strip()
        if not _EMAIL.match(value):
            raise ValueError("participant_email must be an email address")
        return value


class WalkInTicket(BaseModel):
    """The normal ticket: the registration reference IS the QR value the existing check-in scans."""

    registration_reference: str | None = None
    qr_code: str | None = None
    qr_image_path: str | None = Field(None, description="GET this path (existing endpoint) for the QR image.")
    ticket_type_id: str | None = None
    ticket_type_name: str | None = None


class WalkInPayment(BaseModel):
    required: bool = Field(..., description="False for a free event or a ticket that costs nothing.")
    status: AttendeePaymentStatus = Field(
        ..., description="free | paid (nothing owed) | pending (a priced ticket: no payment has been recorded)."
    )
    amount: float | None = Field(None, description="The ticket price, from the existing ticket-price rules (order total).")
    currency: str | None = None
    order_id: UUID | None = None
    note: str | None = Field(
        None,
        description="Set when payment is pending: the backend has no offline/manual payment mode, so nothing is marked paid.",
    )


class WalkInCheckIn(BaseModel):
    requested: bool
    performed: bool
    checked_in_at: datetime | None = None
    reason: Literal["payment_pending"] | None = Field(None, description="Why a requested check-in was not performed.")


class WalkInSessionCheckIn(BaseModel):
    requested: bool
    session_id: str | None = None
    performed: bool
    reason: Literal["payment_pending"] | None = None


class EventWalkInResponse(BaseModel):
    message: str
    registration: EventAttendeeResponse = Field(
        ..., description="The registration exactly as the attendee list shows it (registration_source is walk_in)."
    )
    ticket: WalkInTicket
    payment: WalkInPayment
    check_in: WalkInCheckIn
    session_check_in: WalkInSessionCheckIn
