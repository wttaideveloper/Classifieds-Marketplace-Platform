from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE

NotificationType = Literal["nudge", "automatic", "manual"]
NotificationCategory = Literal[
    "general",
    "water_intake",
    "meal_logging",
    "weight_logging",
    "mood_tracking",
    "health_assessment",
    "recommendation",
    "new_video",
    "new_event",
    "new_training",
    "new_book",
    "new_message",
    "event_reminder",
    "payment_successful",
    "booking_confirmed",
    "chat_message",
    # System-generated workflow notifications (notification_type="automatic") — see
    # docs/training-workflow-notifications.md. Clients never create these via the send endpoints.
    "event_submitted",
    "event_approved",
    "event_rejected",
    "event_changes_requested",
    "training_submitted",
    "training_approved",
    "training_rejected",
    "training_changes_requested",
    "training_enrolled",
    "training_enrollment_accepted",
    "training_enrollment_rejected",
    "training_enrolment_confirmation",
    "enrolment_cancelled",
    "training_new",
    "training_certificate",
    "training_announcement",
    "training_answer",
    "training_reminder",
    "training_final_day",
    "review_submitted",
    "review_approved",
    "review_rejected",
]

_TYPE_DOC = (
    "nudge | manual | automatic. `nudge` and `manual` are what the send endpoints create. "
    "`automatic` is system-generated and reserved for server-side triggers — its meaning is carried by "
    "`category` (e.g. training_submitted, training_approved, training_rejected, training_changes_requested, "
    "training_enrolled, training_enrollment_accepted, training_enrollment_rejected; see NotificationCategory)."
)
_CATEGORY_DOC = (
    "Free-form for manual notifications. For system-generated (`automatic`) notifications it is the event "
    "type, and the same value is repeated in `metadata.category` so push `data` can be routed. Training "
    "approval/enrollment types: training_submitted (to Platform/Super Admins), training_approved | "
    "training_rejected | training_changes_requested (to the owning Enterprise Admins), training_enrolled "
    "(to the owning Enterprise Admins), training_enrollment_accepted | training_enrollment_rejected (to the "
    "learner). Their metadata always has training_id, entity_type=\"training\", entity_id and status; "
    "enrollment notifications add enrollment_id; rejections add reason when one was given. Review types: "
    "review_submitted (to the owning Enterprise Admins when a new training/product/service/event review is saved), "
    "review_approved | review_rejected (to the review's author). Their metadata has entity_type "
    "(training|product|service|event), entity_id, the matching <module>_id, review_id and status; "
    "review_submitted adds rating."
)
DeliveryType = Literal["immediate", "scheduled"]
NotificationStatus = Literal["draft", "scheduled", "processing", "sent", "failed", "cancelled"]
DeliveryChannel = Literal["in_app", "push", "email", "sms"]


class NotificationCreate(BaseModel):
    title: str = Field(..., max_length=255)
    message: str
    notification_type: NotificationType = Field("manual", description=_TYPE_DOC)
    category: str = Field("general", description=_CATEGORY_DOC)
    delivery_type: DeliveryType = "immediate"
    scheduled_at: datetime | None = None
    tenant_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NotificationUpdate(BaseModel):
    title: str | None = Field(None, max_length=255)
    message: str | None = None
    notification_type: NotificationType | None = Field(None, description=_TYPE_DOC)
    category: str | None = Field(None, description=_CATEGORY_DOC)
    delivery_type: DeliveryType | None = None
    scheduled_at: datetime | None = None
    status: NotificationStatus | None = None
    metadata: dict[str, Any] | None = None


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None
    created_by: UUID | None
    title: str
    message: str
    notification_type: str = Field(..., description=_TYPE_DOC)
    category: str = Field(..., description=_CATEGORY_DOC)
    delivery_type: str
    scheduled_at: datetime | None
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class NotificationPaginatedResponse(BaseModel):
    items: list[NotificationResponse]
    pagination: dict


_TRAINING_ID_EXAMPLE = "62078973-39ac-46e5-b867-6196935025ba"
_ENROLLMENT_ID_EXAMPLE = "a972be06-bdb7-4307-ae7c-7a734f234662"


def _feed_example(category: str, title: str, message: str, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "0b6f3c1e-5d1a-4a8e-9a53-0d2f6d4a7c11",
        "notification_id": "4e7d2c90-1f3b-4c55-8d0a-6b1e9a2f3c77",
        "user_id": "9d1f7a52-3c4e-4b86-a0d5-2e8c6f1b7a90",
        "is_read": False, "read_at": None, "delivered_at": None,
        "title": title, "message": message,
        "notification_type": "automatic", "category": category,
        "metadata": {"category": category, **metadata},
        "created_at": "2026-10-05T10:15:00",
    }


_TRAINING_ENTITY = {"training_id": _TRAINING_ID_EXAMPLE, "entity_type": "training", "entity_id": _TRAINING_ID_EXAMPLE}


class UserNotificationResponse(BaseModel):
    """One item of the user's notification feed (GET /users/me/notifications). The same record is
    pushed live as the generic Socket.IO `notification` event and, with a reduced `data` block, as
    an FCM push — see docs/training-workflow-notifications.md."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"examples": [
            _feed_example("training_submitted", "Training submitted for approval",
                          '"Ergonomics 101" was submitted for approval.', {**_TRAINING_ENTITY, "status": "pending_approval"}),
            _feed_example("training_changes_requested", "Training changes requested",
                          'Changes were requested for "Ergonomics 101".',
                          {**_TRAINING_ENTITY, "status": "needs_revision", "reason": "Please add the agenda"}),
            _feed_example("training_enrolled", "New enrollment", 'A learner enrolled in "Ergonomics 101".',
                          {**_TRAINING_ENTITY, "enrollment_id": _ENROLLMENT_ID_EXAMPLE, "status": "enrolled"}),
            _feed_example("training_enrollment_rejected", "Enrollment rejected",
                          'Your enrollment in "Ergonomics 101" was not accepted.',
                          {**_TRAINING_ENTITY, "enrollment_id": _ENROLLMENT_ID_EXAMPLE, "status": "rejected", "reason": "Seat full"}),
        ]},
    )

    id: UUID
    notification_id: UUID
    user_id: UUID
    is_read: bool
    read_at: datetime | None
    delivered_at: datetime | None
    title: str
    message: str
    notification_type: str = Field(..., description=_TYPE_DOC)
    category: str = Field(..., description=_CATEGORY_DOC)
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Routing/context fields. Always includes `category`. Workflow notifications add "
        "training_id, entity_type, entity_id, status, enrollment_id (enrollment events) and reason (when relevant).",
    )
    created_at: datetime


class UserNotificationPaginatedResponse(BaseModel):
    items: list[UserNotificationResponse]
    pagination: dict


class UnreadNotificationCountResponse(BaseModel):
    unread_count: int


class MarkReadResponse(BaseModel):
    id: UUID
    is_read: bool
    read_at: datetime | None


class MarkAllReadResponse(BaseModel):
    marked_read: int


class SendNotificationRequest(BaseModel):
    title: str = Field(..., max_length=255)
    message: str
    notification_type: NotificationType = "manual"
    category: str = "general"
    user_ids: list[UUID] = Field(..., min_length=1)
    tenant_id: UUID | None = None
    channels: list[DeliveryChannel] = Field(default_factory=lambda: ["in_app", "push"])
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScheduleNotificationRequest(SendNotificationRequest):
    scheduled_at: datetime


class SendToTenantRequest(BaseModel):
    title: str = Field(..., max_length=255)
    message: str
    tenant_id: UUID
    notification_type: NotificationType = "manual"
    category: str = "general"
    channels: list[DeliveryChannel] = Field(default_factory=lambda: ["in_app", "push"])
    metadata: dict[str, Any] = Field(default_factory=dict)


class SendToUsersRequest(BaseModel):
    title: str = Field(..., max_length=255)
    message: str
    user_ids: list[UUID] = Field(..., min_length=1)
    notification_type: NotificationType = "manual"
    category: str = "general"
    tenant_id: UUID | None = None
    channels: list[DeliveryChannel] = Field(default_factory=lambda: ["in_app", "push"])
    metadata: dict[str, Any] = Field(default_factory=dict)


class SendToGroupsRequest(BaseModel):
    title: str = Field(..., max_length=255)
    message: str
    group_user_ids: list[UUID] = Field(..., min_length=1, description="User IDs in the target group.")
    notification_type: NotificationType = "manual"
    category: str = "general"
    tenant_id: UUID | None = None
    channels: list[DeliveryChannel] = Field(default_factory=lambda: ["in_app", "push"])
    metadata: dict[str, Any] = Field(default_factory=dict)


class SendNotificationResult(BaseModel):
    notification_id: UUID
    recipients: int
    delivered: int
    status: str


class NotificationTemplateCreate(BaseModel):
    template_name: str = Field(..., max_length=150)
    category: str
    title: str = Field(..., max_length=255)
    message: str
    tenant_id: UUID | None = None
    is_active: bool = True


class NotificationTemplateUpdate(BaseModel):
    template_name: str | None = Field(None, max_length=150)
    category: str | None = None
    title: str | None = Field(None, max_length=255)
    message: str | None = None
    is_active: bool | None = None


class NotificationTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None
    template_name: str
    category: str
    title: str
    message: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class NotificationTemplatePaginatedResponse(BaseModel):
    items: list[NotificationTemplateResponse]
    pagination: dict


class NotificationListQuery(BaseModel):
    page: int = DEFAULT_PAGE
    page_size: int = DEFAULT_PAGE_SIZE
    tenant_id: UUID | None = None
    status: NotificationStatus | None = None
    notification_type: NotificationType | None = None
    category: str | None = None
