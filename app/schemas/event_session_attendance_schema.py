"""Request/response schemas for session-level attendance (Phase 2.4).

Session attendance is separate from, and never changes, event-level check-in (EventCheckInRequest /
EventCheckInResponse in event_schema.py).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Largest single batch. Each attendee is validated independently; this only bounds the request size.
MAX_SESSION_BATCH = 1000


# --------------------------------------------------------------------------- requests


class EventSessionCheckInRequest(BaseModel):
    registration_id: UUID | None = Field(None, description="Registration ID to check in")
    qr_code: str | None = Field(None, max_length=255, description="The attendee's existing registration QR value (used when registration_id is not sent)")
    method: str | None = Field("manual", max_length=30, description="Recorded in the audit trail: manual, qr_code, nfc")

    model_config = ConfigDict(json_schema_extra={"example": {"qr_code": "5B652615A1C4", "method": "qr_code"}})


class EventSessionUncheckInRequest(BaseModel):
    registration_id: UUID | None = Field(None, description="Registration ID whose session check-in is undone")
    qr_code: str | None = Field(None, max_length=255, description="Registration QR value (used when registration_id is not sent)")
    reason: str | None = Field(None, max_length=500, description="Reason for undoing the check-in (kept in the audit trail)")


class EventSessionCheckOutRequest(BaseModel):
    registration_id: UUID | None = Field(None, description="Registration ID to check out of the session")
    qr_code: str | None = Field(None, max_length=255, description="Registration QR value (used when registration_id is not sent)")


class EventSessionBatchCheckInItem(BaseModel):
    registration_id: UUID | None = Field(None, description="Registration ID")
    qr_code: str | None = Field(None, max_length=255, description="Registration QR value")


class EventSessionBatchCheckInRequest(BaseModel):
    participants: list[EventSessionBatchCheckInItem] = Field(
        ..., min_length=1, max_length=MAX_SESSION_BATCH, description="Attendees to check in to the session; each is validated on its own"
    )


# --------------------------------------------------------------------------- responses


SessionAttendanceOutcome = Literal["checked_in", "already_checked_in", "unchecked_in", "checked_out", "already_checked_out"]


class EventSessionAttendanceResponse(BaseModel):
    """The attendee's state for one session after a check-in / undo / check-out."""

    message: str
    outcome: SessionAttendanceOutcome = Field(
        ..., description="checked_in | already_checked_in (repeat scan, nothing changed) | unchecked_in | checked_out | already_checked_out"
    )
    event_id: UUID
    session_id: str
    session_title: str | None = None
    registration_id: UUID
    participant_name: str | None = None
    participant_email: str | None = None
    registration_status: str | None = Field(None, description="The event-level registration status; session attendance never changes it")
    checked_in: bool
    checked_in_at: datetime | None = None
    checked_in_by: UUID | None = None
    checked_out_at: datetime | None = None
    checked_out_by: UUID | None = None


BatchItemStatus = Literal["checked_in", "already_checked_in", "failed"]


class EventSessionBatchCheckInResultItem(BaseModel):
    registration_id: UUID | None = Field(None, description="Null when the supplied identifier matched no registration of this event")
    qr_code: str | None = Field(None, description="The QR value supplied for this entry, if any")
    participant_name: str | None = None
    participant_email: str | None = None
    status: BatchItemStatus
    checked_in_at: datetime | None = None
    message: str


class EventSessionBatchCheckInResponse(BaseModel):
    event_id: UUID
    session_id: str
    total: int
    succeeded: int = Field(..., description="checked_in + already_checked_in")
    failed: int
    results: list[EventSessionBatchCheckInResultItem]


# --------------------------------------------------------------------------- read models (attendee detail, dashboard)


class EventSessionAttendanceState(BaseModel):
    """One session's attendance for one attendee (Phase 2.3 attendee list / detail)."""

    session_id: str
    title: str | None = None
    checked_in: bool
    checked_in_at: datetime | None = None
    checked_in_by: UUID | None = None
    checked_out_at: datetime | None = None


class EventSessionAttendanceSummary(BaseModel):
    """Per-session counts (dashboard). ``registered_count`` is the event's active registrations, because a
    registration is for the event, not for one session; ``checked_in_count`` counts only attendees whose
    registration is still active, so the percentage can never exceed 100."""

    session_id: str
    title: str | None = None
    session_date: str | None = None
    start_time: str | None = None
    registered_count: int
    checked_in_count: int
    attendance_percentage: float | None = Field(None, description="checked_in / registered, 1 decimal; null when nobody is registered")
