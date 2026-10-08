"""Cross-module review moderation (GET /reviews/manage, PATCH /reviews/{module}/{review_id}/moderate)."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common_schema import PaginationMeta


class ReviewQueueItem(BaseModel):
    module: str = Field(..., description="training | product | service | event (a course is a training)")
    review_id: UUID
    item_id: UUID = Field(..., description="The training / product / service / event the review is about")
    item_name: str | None = None
    rating: int | None = Field(None, description="1-5; null only for an old record whose stored rating is not a valid number")
    comment: str | None = None
    reviewer_name: str | None = Field(None, description="Never an email address; null when no name is known")
    reviewer_email: str | None = Field(None, description="Training and event reviews only. For moderators; do not show it publicly")
    reviewer_user_id: UUID | None = Field(None, description="Product, service and event reviews")
    is_verified: bool = Field(..., description="Training/event: always true (registered or enrolled). Product: confirmed purchase. Service: always false")
    tenant_id: UUID | None = Field(None, description="The business that owns the item")
    business_name: str | None = Field(None, description="Short name of that business (for the Super Admin's global page)")
    moderation_status: str = Field(..., description="pending | approved | rejected")
    created_at: datetime
    updated_at: datetime


class ReviewQueueCounts(BaseModel):
    pending: int = 0
    approved: int = 0
    rejected: int = 0


class ReviewQueueResponse(BaseModel):
    items: list[ReviewQueueItem] = Field(default_factory=list)
    counts: ReviewQueueCounts = Field(..., description="Reviews per status for the current filters, ignoring `status`; for the Pending / Approved / Rejected tabs")
    pagination: PaginationMeta


class ReviewModerateBody(BaseModel):
    action: str = Field(..., description="approved | rejected | pending")


class ReviewAuditItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    module: str = Field(..., description="training | product | service | event")
    review_id: UUID
    item_id: UUID
    item_name: str | None = None
    from_status: str | None = Field(None, description="The status before the change")
    to_status: str = Field(..., description="approved | rejected | pending, or deleted when a moderator removed the review")
    actor_user_id: UUID | None = Field(None, description="Who did it")
    actor_role: str | None = None
    created_at: datetime


class ReviewAuditResponse(BaseModel):
    items: list[ReviewAuditItem] = Field(default_factory=list)
    pagination: PaginationMeta
