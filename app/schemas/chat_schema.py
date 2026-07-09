from datetime import date, datetime
from typing import Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common_schema import PaginatedResponse

ConversationStatus = Literal["open", "closed", "archived"]
ConversationType = Literal["standard", "preview", "booking"]
ParticipantRole = Literal["customer", "provider", "admin"]
MessageType = Literal["text", "image", "document", "audio", "video", "system"]
AttachmentType = Literal["image", "document", "audio", "video"]
PresenceStatus = Literal["online", "offline", "away"]
DevicePlatform = Literal["ios", "android", "web"]
PlanType = Literal["free", "basic", "premium"]

T = TypeVar("T")


class CursorPaginationMeta(BaseModel):
    has_more: bool = Field(..., description="Whether more records exist.")
    next_cursor: str | None = Field(None, description="Cursor for loading older messages.")
    limit: int = Field(..., description="Number of items requested.")


class CursorPaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    pagination: CursorPaginationMeta


# --- Participants ---

class ParticipantInput(BaseModel):
    user_id: UUID
    role: ParticipantRole = "customer"


class ParticipantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    role: ParticipantRole
    last_read_at: datetime | None = None
    joined_at: datetime


# --- Conversations ---

_CONVERSATION_CREATE_EXAMPLE = {
    "subject": "Question about yoga classes",
    "conversation_type": "standard",
    "context_type": "service",
    "context_id": "550e8400-e29b-41d4-a716-446655440010",
    "participant_ids": [
        {"user_id": "550e8400-e29b-41d4-a716-446655440020", "role": "provider"},
    ],
}


class ConversationCreate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": _CONVERSATION_CREATE_EXAMPLE})

    subject: str | None = Field(None, max_length=255, description="Optional conversation subject.")
    conversation_type: ConversationType = Field(
        "standard",
        description="Chat type: standard, preview (limited free messages), or booking.",
    )
    context_type: str | None = Field(None, description="Related entity type (service, product, enterprise).")
    context_id: UUID | None = Field(None, description="Related entity ID.")
    tenant_id: UUID | None = Field(None, description="Optional tenant scope.")
    participant_ids: list[ParticipantInput] = Field(
        default_factory=list,
        description="Additional participants to add to the conversation.",
    )
    provider_id: UUID | None = Field(None, description="Optional provider to assign immediately.")


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None = None
    status: ConversationStatus
    conversation_type: ConversationType
    is_read_only: bool
    subject: str | None = None
    context_type: str | None = None
    context_id: UUID | None = None
    assigned_provider_id: UUID | None = None
    expires_at: datetime | None = None
    last_message_at: datetime | None = None
    last_message_preview: str | None = None
    created_by: UUID
    unread_count: int = 0
    participants: list[ParticipantResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ConversationListItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: ConversationStatus
    conversation_type: ConversationType
    subject: str | None = None
    last_message_at: datetime | None = None
    last_message_preview: str | None = None
    unread_count: int = 0
    assigned_provider_id: UUID | None = None
    is_archived: bool = False
    archived_at: datetime | None = None
    updated_at: datetime


class ConversationPaginatedResponse(PaginatedResponse[ConversationListItemResponse]):
    pass


class ConversationArchiveRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"archived": True},
                {"archived": False},
            ]
        }
    )

    archived: bool = Field(..., description="Set true to archive, false to unarchive.")


class ConversationArchiveResponse(BaseModel):
    id: UUID
    status: ConversationStatus
    is_archived: bool
    archived_at: datetime | None = None
    updated_at: datetime


class ConversationStatusUpdateResponse(BaseModel):
    id: UUID
    status: ConversationStatus
    updated_at: datetime


# --- Messages ---

class MessageCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
                "content": "Hello, I have a question about your services.",
                "message_type": "text",
            }
        }
    )

    conversation_id: UUID
    content: str | None = Field(None, description="Message text content.")
    message_type: MessageType = "text"
    attachment_id: UUID | None = Field(None, description="Pre-uploaded attachment ID.")


class MessageUpdate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "content": "Updated message text.",
            }
        }
    )

    content: str = Field(..., min_length=1, description="New message text.")


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    sender_id: UUID
    content: str | None = None
    message_type: MessageType
    attachment_id: UUID | None = None
    is_deleted: bool
    is_edited: bool = False
    edited_at: datetime | None = None
    created_at: datetime
    read_by: list[UUID] = Field(default_factory=list)


class MessageReadResponse(BaseModel):
    message_id: UUID
    user_id: UUID
    read_at: datetime


class MessageReadStatusResponse(BaseModel):
    message_id: UUID
    total_recipients: int
    read_count: int
    read_by: list[MessageReadResponse]


class MessageDeleteResponse(BaseModel):
    id: UUID
    is_deleted: bool
    deleted_at: datetime


# --- Attachments ---

class AttachmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    message_id: UUID | None = None
    uploaded_by: UUID
    file_name: str
    mime_type: str
    file_size: int
    attachment_type: AttachmentType
    download_url: str
    transcript: str | None = None
    transcribed_at: datetime | None = None
    created_at: datetime


class AttachmentDeleteResponse(BaseModel):
    id: UUID
    is_deleted: bool
    message: str


class TranscribeRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
                "message_id": "550e8400-e29b-41d4-a716-446655440010",
                "attachment_id": "550e8400-e29b-41d4-a716-446655440011",
            }
        }
    )

    conversation_id: UUID | None = Field(
        None,
        description="Optional conversation ID for extra validation.",
    )
    message_id: UUID | None = Field(
        None,
        description="Optional message ID linked to the audio attachment.",
    )
    attachment_id: UUID | None = Field(
        None,
        description="Audio attachment to transcribe. Required for message route if message has no attachment_id.",
    )


class TranscribeResponse(BaseModel):
    transcript: str = Field(..., description="Speech-to-text result.")
    conversation_id: UUID
    message_id: UUID | None = None
    attachment_id: UUID
    mime_type: str
    already_transcribed: bool = Field(
        False,
        description="True when an existing saved transcript was returned without re-processing.",
    )
    transcribed_at: datetime | None = None


# --- Provider Assignment ---

class ProviderAssignRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
                "provider_id": "550e8400-e29b-41d4-a716-446655440020",
            }
        }
    )

    conversation_id: UUID
    provider_id: UUID


class ProviderReassignRequest(BaseModel):
    conversation_id: UUID
    provider_id: UUID


class ProviderAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    provider_id: UUID
    assigned_by: UUID
    status: str
    assigned_at: datetime
    reassigned_at: datetime | None = None


# --- Notifications ---

class DeviceRegisterRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "token": "fcm-device-token-here",
                "platform": "android",
            }
        }
    )

    token: str = Field(..., min_length=1, max_length=500)
    platform: DevicePlatform


class DeviceTokenResponse(BaseModel):
    id: UUID
    token: str
    platform: DevicePlatform
    is_active: bool
    created_at: datetime


class NotificationPreferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    push_enabled: bool
    email_enabled: bool
    in_app_enabled: bool
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None
    updated_at: datetime


class NotificationPreferenceUpdate(BaseModel):
    push_enabled: bool | None = None
    email_enabled: bool | None = None
    in_app_enabled: bool | None = None
    quiet_hours_start: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    quiet_hours_end: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")


class UnreadCountResponse(BaseModel):
    unread_messages: int
    unread_notifications: int
    total_unread: int


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    notification_type: str
    title: str
    body: str
    data: dict = Field(default_factory=dict)
    is_read: bool
    created_at: datetime


class NotificationPaginatedResponse(PaginatedResponse[NotificationResponse]):
    pass


# --- Presence ---

class PresenceUpdateRequest(BaseModel):
    status: PresenceStatus


class PresenceResponse(BaseModel):
    user_id: UUID
    status: PresenceStatus
    last_seen_at: datetime


class OnlineUsersResponse(BaseModel):
    users: list[PresenceResponse]
    total: int


# --- Typing ---

class TypingUpdateRequest(BaseModel):
    is_typing: bool = True


class TypingIndicatorResponse(BaseModel):
    conversation_id: UUID
    user_id: UUID
    is_typing: bool
    updated_at: datetime


# --- Subscriptions ---

class ChatEligibilityResponse(BaseModel):
    eligible: bool
    plan_type: PlanType
    reason: str | None = None
    remaining_messages: int
    monthly_limit: int
    messages_used: int
    period_end: date


class MonthlyLimitResponse(BaseModel):
    monthly_limit: int
    messages_used: int
    remaining_messages: int
    period_start: date
    period_end: date


# --- Admin ---

class ChatDashboardResponse(BaseModel):
    total_conversations: int
    active_conversations: int
    closed_conversations: int
    archived_conversations: int
    total_messages_today: int
    total_unread_messages: int


class ChatExportResponse(BaseModel):
    conversation_id: UUID
    exported_at: datetime
    message_count: int
    messages: list[MessageResponse]
