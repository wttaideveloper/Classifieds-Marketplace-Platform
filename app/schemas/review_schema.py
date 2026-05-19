from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from uuid import UUID
from datetime import datetime
from enum import Enum


class ReviewModerationStatus(str, Enum):
    pending = "Pending"
    approved = "Approved"
    rejected = "Rejected"


class ReviewCreateSchema(BaseModel):
    business_id: UUID = Field(..., examples=["business-uuid-here"])
    booking_id: UUID = Field(..., examples=["booking-uuid-here"])
    rating: int = Field(..., ge=1, le=5, examples=[5])
    review_comment: Optional[str] = Field(None, examples=["Great service!"])
    listing_id: Optional[UUID] = Field(None, examples=["listing-uuid-here"])


class ReviewModerationSchema(BaseModel):
    moderation_status: ReviewModerationStatus = Field(..., examples=["Approved"])
    remarks: Optional[str] = Field(None, examples=["Reviewed and approved"])


class ReviewResponse(BaseModel):
    review_id: str
    customer_name: Optional[str]
    rating: int
    review_comment: Optional[str]
    created_at: datetime


class PaginatedReviewsResponse(BaseModel):
    page: int
    size: int
    total_elements: int
    total_pages: int
    average_rating: float
    data: List[ReviewResponse]


class ReviewSubmissionResponse(BaseModel):
    review_id: str
    moderation_status: ReviewModerationStatus
    created_at: datetime
