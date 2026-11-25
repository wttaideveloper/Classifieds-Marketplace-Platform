"""Blog engagement request / response shapes. The contract is explained in docs/blog-engagement.md."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

ReportReason = Literal["unsafe_medical", "harassment", "offensive", "spam", "other"]
CommentStatus = Literal["published", "held_for_review", "pending_moderation", "removed_by_ai", "removed_by_admin", "deleted_by_author"]
Queue = Literal["auto_removed", "awaiting_review", "user_reports", "ea_requests"]


# ---------------------------------------------------------------- requests

class CommentCreate(BaseModel):
    body: str = Field(..., min_length=1, description="Plain text, at most 500 characters.")
    parent_id: UUID | None = Field(None, description="Reply to this comment. A reply to a reply is attached to the top-level comment (replies are one level deep).")


class CommentEdit(BaseModel):
    body: str = Field(..., min_length=1, description="The new text, at most 500 characters. It is checked again like a new comment.")


class ReportCreate(BaseModel):
    reason: ReportReason
    note: str | None = Field(None, max_length=300)


class ReasonBody(BaseModel):
    reason: str | None = Field(None, max_length=500)


class MandatoryReason(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500, description="Required: it goes in the moderation history and, for a removal, to the author.")


class ReviewRequestBody(BaseModel):
    note: str | None = Field(None, max_length=500, description="Why the Super Admin should look again.")


# ---------------------------------------------------------------- customer responses

class EngagementState(BaseModel):
    liked: bool | None = None
    wishlisted: bool | None = None
    likes_count: int
    wishlist_count: int


class CommentAuthor(BaseModel):
    name: str | None = Field(None, description="A display name such as \"Priya S.\": never a full name or an e-mail address.")
    is_me: bool = False


class CommentOut(BaseModel):
    id: UUID
    post_id: UUID
    parent_id: UUID | None = None
    author: CommentAuthor
    body: str | None = Field(None, description="null for a placeholder: the comment was removed or deleted but has replies.")
    status: Literal["published", "pending_review", "unavailable"] = Field(
        ..., description="published, or pending_review (only the author sees their own comment while it waits for the AI or a moderator), or unavailable (a placeholder).")
    created_at: datetime
    is_edited: bool = False
    edited_at: datetime | None = None
    can_edit: bool = False
    can_delete: bool = False
    reported_by_me: bool = False
    author_name: str | None = Field(None, description="Same as author.name: a display name such as \"Priya S.\".")
    like_count: int = 0
    liked_by_me: bool | None = Field(None, description="null when nobody is signed in.")
    reply_count: int = 0
    replies: list["CommentOut"] = Field(default_factory=list, description="Nested to BLOG_COMMENT_MAX_DEPTH levels (3 by default).")


class CommentList(BaseModel):
    items: list[CommentOut]
    pagination: dict
    comments_count: int = Field(..., description="Every published comment and reply on the blog.")


class CommentResult(BaseModel):
    """The answer to posting or editing a comment: 201 / 200 published, 202 held or pending, 422 removed by the AI."""

    outcome: Literal["published", "held_for_review", "pending_moderation", "removed_by_ai"]
    message: str
    comment: CommentOut | None = Field(None, description="Not sent for a 422: a removed comment is not shown to anyone but the admins.")
    comment_id: UUID | None = None


class SavedBlog(BaseModel):
    saved_at: datetime
    blog: dict


# ---------------------------------------------------------------- admin responses

class ModerationComment(BaseModel):
    id: UUID
    post_id: UUID
    post_title: str | None = None
    post_slug: str | None = None
    business_name: str | None = None
    parent_id: UUID | None = None
    author_id: UUID | None = Field(None, description="Super Admin only.")
    author_name: str | None = None
    body: str
    status: CommentStatus
    is_edited: bool = False
    moderation_reason: str | None = None
    moderation_labels: list[str] | None = None
    removed_by_role: str | None = None
    removed_reason: str | None = None
    report_count: int = 0
    report_reasons: dict[str, int] = Field(default_factory=dict)
    review_requested_at: datetime | None = None
    review_request_note: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class LikeSummary(BaseModel):
    like_count: int
    liked_by_me: bool | None = Field(None, description="null when nobody is signed in.")


class CommentLikeState(BaseModel):
    id: UUID
    like_count: int
    liked_by_me: bool


class ReplyPage(BaseModel):
    items: list[CommentOut]
    pagination: dict
