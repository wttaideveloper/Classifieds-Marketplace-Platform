from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ModerationStatusEnum(str, Enum):
    Pending = "Pending"
    Approved = "Approved"
    Rejected = "Rejected"
    Active = "Active"
    Inactive = "Inactive"
    Suspended = "Suspended"


class ReviewModerationStatusEnum(str, Enum):
    Pending = "Pending"
    Approved = "Approved"
    Rejected = "Rejected"


class ReportStatusEnum(str, Enum):
    Pending = "Pending"
    Reviewed = "Reviewed"
    Resolved = "Resolved"


class UpdateEntityStatusSchema(BaseModel):
    status: ModerationStatusEnum = Field(..., description="Target status")
    remarks: Optional[str] = None


class UpdateReviewStatusSchema(BaseModel):
    moderation_status: ReviewModerationStatusEnum
    remarks: Optional[str] = None


class UpdateBlogStatusSchema(BaseModel):
    moderation_status: ReviewModerationStatusEnum = Field(
        ..., description="Approved or Rejected"
    )
    remarks: Optional[str] = None


class UpdateReportStatusSchema(BaseModel):
    report_status: ReportStatusEnum = Field(..., description="Pending | Reviewed | Resolved")
    remarks: Optional[str] = None


class ModerationActionResponse(BaseModel):
    success: bool = True
    message: str
    entity_id: str
    old_status: str
    new_status: str
    updated_by: str
    updated_at: datetime


class ReportedContentResponse(BaseModel):
    id: str
    reported_by: str
    entity_type: str
    entity_id: str
    reason: str
    report_status: str
    reviewed_by: Optional[str] = None
    remarks: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class PaginatedReportsResponse(BaseModel):
    success: bool = True
    page: int
    size: int
    total_elements: int
    total_pages: int
    data: List[ReportedContentResponse]
