from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum
from datetime import datetime


class BlogStatus(str, Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class BlogCreateSchema(BaseModel):
    title: str = Field(..., max_length=255, examples=["My Blog Title"])
    shortDescription: Optional[str] = Field(None, examples=[None])
    content: str = Field(..., examples=["Blog content here"])
    categoryId: Optional[str] = Field(None, examples=[None])
    featuredImage: Optional[str] = Field(None, examples=[None])


class BlogUpdateSchema(BaseModel):
    title: Optional[str] = Field(None, max_length=255, examples=[None])
    shortDescription: Optional[str] = Field(None, examples=[None])
    content: Optional[str] = Field(None, examples=[None])
    categoryId: Optional[str] = Field(None, examples=[None])
    featuredImage: Optional[str] = Field(None, examples=[None])
    status: Optional[BlogStatus] = Field(None, examples=[None])
    isPublished: Optional[bool] = Field(None, examples=[None])


class BlogRejectSchema(BaseModel):
    remarks: str


class BlogResponseSchema(BaseModel):
    id: str
    title: str
    slug: Optional[str] = None
    approvalStatus: ApprovalStatus
    status: BlogStatus
    createdAt: datetime


class PaginatedBlogsResponse(BaseModel):
    success: bool
    message: str
    total: int
    page: int
    limit: int
    data: List[dict]


class BlogCategoryCreateSchema(BaseModel):
    name: str = Field(..., max_length=150, examples=["Technology"])
    description: Optional[str] = Field(None, examples=["Tech related blogs"])


class BlogCategoryUpdateSchema(BaseModel):
    name: Optional[str] = Field(None, max_length=150, examples=[None])
    description: Optional[str] = Field(None, examples=[None])
    isActive: Optional[bool] = Field(None, examples=[None])
