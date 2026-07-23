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
]
DeliveryType = Literal["immediate", "scheduled"]
NotificationStatus = Literal["draft", "scheduled", "processing", "sent", "failed", "cancelled"]
DeliveryChannel = Literal["in_app", "push", "email", "sms"]


class NotificationCreate(BaseModel):
    title: str = Field(..., max_length=255)
    message: str
    notification_type: NotificationType = "manual"
    category: str = "general"
    delivery_type: DeliveryType = "immediate"
    scheduled_at: datetime | None = None
    tenant_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NotificationUpdate(BaseModel):
    title: str | None = Field(None, max_length=255)
    message: str | None = None
    notification_type: NotificationType | None = None
    category: str | None = None
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
    notification_type: str
    category: str
    delivery_type: str
    scheduled_at: datetime | None
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class NotificationPaginatedResponse(BaseModel):
    items: list[NotificationResponse]
    pagination: dict


class UserNotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    notification_id: UUID
    user_id: UUID
    is_read: bool
    read_at: datetime | None
    delivered_at: datetime | None
    title: str
    message: str
    notification_type: str
    category: str
    metadata: dict[str, Any] = Field(default_factory=dict)
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
