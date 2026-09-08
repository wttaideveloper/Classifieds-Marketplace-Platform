from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common_schema import PaginatedResponse

BlogStatus = Literal["draft", "published", "archived"]


class BlogCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "title": "Getting Started with Spin Health",
                "slug": "getting-started-with-spin-health",
                "excerpt": "A short intro for the listing card.",
                "content": "<p>Full blog HTML/markdown content…</p>",
                "cover_image_url": "https://cdn.example.com/blogs/cover.jpg",
                "author_name": "Platform Team",
                "category": "Guides",
                "tags": ["health", "onboarding"],
                "status": "draft",
                "seo_title": "Getting Started | Spin Health",
                "seo_description": "Learn how to get started on the platform.",
            }
        }
    )

    title: str = Field(..., min_length=1, max_length=255)
    slug: str | None = Field(None, max_length=255, description="URL slug; auto-generated from title if omitted")
    excerpt: str | None = None
    content: str = Field(..., min_length=1)
    cover_image_url: str | None = None
    author_name: str | None = None
    author_id: UUID | None = None
    category: str | None = None
    tags: list[str] | None = Field(default_factory=list)
    status: BlogStatus = "draft"
    seo_title: str | None = None
    seo_description: str | None = None
    tenant_id: UUID | None = None
    enterprise_id: UUID | None = None
    published_at: datetime | None = None

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value):
        if value is None:
            return []
        return value


class BlogUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    slug: str | None = Field(None, max_length=255)
    excerpt: str | None = None
    content: str | None = Field(None, min_length=1)
    cover_image_url: str | None = None
    author_name: str | None = None
    author_id: UUID | None = None
    category: str | None = None
    tags: list[str] | None = None
    status: BlogStatus | None = None
    seo_title: str | None = None
    seo_description: str | None = None
    tenant_id: UUID | None = None
    enterprise_id: UUID | None = None
    published_at: datetime | None = None


class BlogStatusUpdate(BaseModel):
    status: BlogStatus


class BlogListItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    slug: str
    excerpt: str | None = None
    cover_image_url: str | None = None
    author_name: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    status: BlogStatus
    published_at: datetime | None = None
    view_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BlogDetailResponse(BlogListItemResponse):
    content: str
    author_id: UUID | None = None
    seo_title: str | None = None
    seo_description: str | None = None
    tenant_id: UUID | None = None
    enterprise_id: UUID | None = None
    created_by: str | None = None
    updated_by: str | None = None


class BlogPaginatedResponse(PaginatedResponse[BlogListItemResponse]):
    pass
