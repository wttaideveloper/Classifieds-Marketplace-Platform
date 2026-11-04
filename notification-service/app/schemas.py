from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Channel = Literal["in_app", "push", "email"]
Priority = Literal["normal", "high", "critical"]


class Recipient(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64)
    email: str | None = Field(None, max_length=255, description="Needed only for the email channel")


class EventIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "event": "payment.succeeded", "recipients": [{"user_id": "u-123", "email": "priya@example.com"}], "tenant_id": "t-1",
        "idempotency_key": "payment-97eb:succeeded", "data": {"amount": "1180.00", "currency": "INR", "payment_id": "97eb"}}})

    event: str = Field(..., min_length=1, max_length=100, description="A registered event type, for example payment.succeeded")
    recipients: list[Recipient] = Field(..., min_length=1, max_length=1000)
    data: dict[str, Any] = Field(default_factory=dict, description="Values for the template's {placeholders}, and what the app gets with the notification")
    tenant_id: str | None = Field(None, max_length=64)
    idempotency_key: str | None = Field(None, max_length=120, description="Sending the same key again for the same person does nothing")
    channels: list[Channel] | None = Field(None, description="Overrides the event type's channels")
    title: str | None = Field(None, max_length=255, description="One-off wording; lets you send an event that has no registered type")
    body: str | None = None


class EventResult(BaseModel):
    user_id: str
    status: Literal["created", "duplicate"]
    notification_id: str | None = None
    channels: list[str] = []


class EventOut(BaseModel):
    results: list[EventResult]


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event: str
    category: str
    title: str
    body: str
    data: dict[str, Any]
    priority: str
    tenant_id: str | None = None
    read_at: datetime | None = None
    created_at: datetime


class Pagination(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    pagination: Pagination
    unread_count: int


class MarkRead(BaseModel):
    ids: list[str] | None = Field(None, description="Which notifications. Leave out with all=true to mark everything")
    all: bool = False


class DeviceIn(BaseModel):
    token: str = Field(..., min_length=10, max_length=512)
    platform: Literal["android", "ios", "web"] = "android"


class PreferenceIn(BaseModel):
    category: str = Field(..., max_length=50)
    channel: Literal["push", "email"]
    enabled: bool


class PreferencesOut(BaseModel):
    items: list[PreferenceIn]


class EventTypeIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "event": "payment.succeeded", "category": "payments", "title": "Payment of {currency} {amount} received", "body": "Thank you.",
        "channels": ["in_app", "push", "email"], "priority": "normal"}})

    event: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9_.\-]+$")
    category: str = Field("general", max_length=50)
    title: str = Field(..., min_length=1, max_length=255)
    body: str = ""
    channels: list[Channel] = Field(default_factory=lambda: ["in_app", "push"])
    priority: Priority = "normal"
    active: bool = True


class EventTypeOut(EventTypeIn):
    model_config = ConfigDict(from_attributes=True)


class NudgeRuleIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "name": "unpaid-order-reminder", "trigger_event": "order.created", "cancel_event": "payment.succeeded",
        "nudge_event": "order.payment_reminder", "delay_minutes": 60, "max_nudges": 2, "repeat_minutes": 1440, "subject_field": "order_id"}})

    name: str = Field(..., min_length=1, max_length=100)
    trigger_event: str
    cancel_event: str | None = Field(None, description="When this event arrives for the same person and subject, the waiting nudge is cancelled")
    nudge_event: str = Field(..., description="The event type that is sent as the reminder")
    delay_minutes: int = Field(..., ge=1, le=60 * 24 * 90)
    max_nudges: int = Field(1, ge=1, le=10)
    repeat_minutes: int | None = Field(None, ge=1, le=60 * 24 * 90)
    subject_field: str = Field("subject_id", max_length=50, description="The key in the event's data that says what the nudge is about, for example order_id")
    active: bool = True


class NudgeRuleOut(NudgeRuleIn):
    model_config = ConfigDict(from_attributes=True)

    id: str


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    notification_id: str
    channel: str
    status: str
    attempts: int
    last_error: str | None = None
    next_attempt_at: datetime
    sent_at: datetime | None = None
    created_at: datetime
