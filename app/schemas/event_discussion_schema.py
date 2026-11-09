from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common_schema import PaginatedResponse

MessageStatus = Literal["pending_moderation", "published", "pending_review", "blocked", "deleted"]
ViewerRole = Literal["participant", "enterprise_admin", "super_admin"]


class DiscussionAccess(BaseModel):
    """What the caller may do in this event's discussion. The Discussion tab is locked while `can_read` is false."""
    event_id: UUID
    event_name: str | None = None
    role: ViewerRole | None = Field(None, description="participant (registered), enterprise_admin (manages the event), super_admin (read-only), or null when locked")
    can_read: bool
    can_post: bool
    can_moderate: bool = Field(..., description="Approve or delete flagged messages: only the event's Enterprise Admins")
    is_closed: bool = Field(False, description="Messages stay readable but nothing new can be posted")
    locked_reason: Literal["NOT_REGISTERED", "REGISTRATION_CANCELLED", "NOT_EVENT_ADMIN", "NOT_SIGNED_IN"] | None = None
    message: str | None = Field(None, description="A sentence to show when the tab is locked or closed")


class MessageCreate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"body": "Is parking available at the venue?", "parent_id": None}})

    body: str = Field(..., min_length=1, description="Plain text. Up to EVENT_DISCUSSION_MAX_CHARS (2000) characters")
    parent_id: UUID | None = Field(None, description="Reply to this published message. Replies are one level deep: a reply to a reply joins the same thread")
    is_announcement: bool = Field(False, description="Enterprise Admin only: an important message, which notifies the participants")


class MessageEdit(BaseModel):
    body: str = Field(..., min_length=1)


class DecisionRequest(BaseModel):
    action: Literal["approve", "delete"]
    note: str | None = Field(None, max_length=500, description="Why, kept in the moderation history")


class AppealRequest(BaseModel):
    note: str | None = Field(None, max_length=500, description="Why the message should be allowed")


class Author(BaseModel):
    id: str
    name: str | None = None
    role: Literal["participant", "enterprise_admin"]


class MessageOut(BaseModel):
    id: UUID
    event_id: UUID
    parent_id: UUID | None = None
    root_id: UUID | None = None
    body: str
    author: Author
    is_announcement: bool = False
    status: MessageStatus = Field(..., description="Other people only ever see published messages. You also see your own that are still being checked, waiting for review, blocked or deleted")
    status_message: str | None = Field(None, description="What to tell the author about a message that is not published")
    is_mine: bool = False
    is_appeal: bool = False
    reply_count: int = 0
    is_unread_reply: bool = Field(False, description="A reply to the caller's message that the caller has not opened yet")
    created_at: datetime
    published_at: datetime | None = None


class MessageResult(BaseModel):
    """The answer to posting: the message with its outcome, and the sentence to show."""
    message: MessageOut
    user_message: str | None = Field(None, description="Show this to the sender. null when the message was published")


class MessagePage(PaginatedResponse[MessageOut]):
    pass


class MessageDetail(MessageOut):
    replies: list[MessageOut] = Field(default_factory=list, description="The thread's replies the caller may see, oldest first")


class ModerationItem(BaseModel):
    """A message in the review list, with what the Enterprise Admin needs to decide."""
    message_id: UUID
    event_id: UUID
    event_name: str | None = None
    sender_id: str
    sender_name: str | None = None
    sender_role: Literal["participant", "enterprise_admin"]
    body: str
    status: MessageStatus
    is_appeal: bool = False
    flagging_reason: str | None = Field(None, description="Why the AI flagged it")
    submitted_at: datetime
    decided_by_name: str | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    parent_id: UUID | None = None


class ModerationPage(PaginatedResponse[ModerationItem]):
    pending_count: int = Field(0, description="Messages waiting for review in this list's scope")


class HistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    message_id: UUID
    action: str
    from_status: str | None = None
    to_status: str | None = None
    label: str | None = None
    reason: str | None = None
    actor_id: str | None = None
    actor_name: str | None = None
    actor_role: str | None = Field(None, description="participant, enterprise_admin, or system for the AI")
    created_at: datetime


class HistoryPage(PaginatedResponse[HistoryItem]):
    pass


class EventActivity(BaseModel):
    event_id: UUID
    event_name: str | None = None
    tenant_id: UUID | None = None
    published: int = 0
    pending_review: int = 0
    blocked: int = 0
    deleted: int = 0
    pending_moderation: int = 0
    last_activity_at: datetime | None = None


class EventActivityPage(PaginatedResponse[EventActivity]):
    pass
