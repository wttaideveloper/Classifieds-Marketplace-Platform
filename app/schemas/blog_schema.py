from datetime import datetime
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common_schema import PaginatedResponse

BlogKind = Literal["blog", "news"]
BlogSource = Literal["platform", "merchant"]
BlogStatus = Literal["draft", "pending_approval", "changes_requested", "rejected", "published", "unpublished", "deleted"]
# "archived" is the old name of "unpublished"; the status endpoint still accepts it.
BlogStatusInput = Literal["draft", "published", "unpublished", "archived"]
ReviewAction = Literal["approve", "request_changes", "reject"]

TITLE_MAX, SUMMARY_MAX, BODY_MAX, TAGS_MAX, TAG_MAX = 150, 300, 100_000, 10, 30


def _check_url(value: str | None) -> str | None:
    if value is None:
        return None
    url = value.strip()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https", "") or url.startswith("//"):
        raise ValueError("must be an http(s) address or a path on this site")
    return url


def _clean_tags(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("tags must be a list")
    tags: list[str] = []
    for raw in value:
        tag = " ".join(str(raw).split())
        if not tag:
            continue
        if len(tag) > TAG_MAX:
            raise ValueError(f"a tag can have at most {TAG_MAX} characters")
        if tag.lower() not in {t.lower() for t in tags}:
            tags.append(tag)
    if len(tags) > TAGS_MAX:
        raise ValueError(f"at most {TAGS_MAX} tags")
    return tags


class _PostFields(BaseModel):
    kind: BlogKind | None = Field(None, description="blog | news. Decides which list the post shows in")
    title: str | None = Field(None, max_length=TITLE_MAX)
    excerpt: str | None = Field(None, max_length=SUMMARY_MAX, description="The short summary shown on the list card")
    content: str | None = Field(None, max_length=BODY_MAX, description="The body: formatted text (headings, bold, italic, lists, links, images). Anything else is removed")
    cover_image_url: str | None = Field(None, description="One cover image (upload it with POST /uploads first)")
    category_id: UUID | None = Field(None, description="An active category from GET /cms/blogs/categories")
    category: str | None = Field(None, max_length=100, description="Alternative to category_id: the name of an active category")
    tags: list[str] | None = Field(None, description="Optional, up to 10 short tags")

    @field_validator("cover_image_url")
    @classmethod
    def _cover(cls, value):
        return _check_url(value)

    @field_validator("tags", mode="before")
    @classmethod
    def _tags(cls, value):
        return None if value is None else _clean_tags(value)


class MerchantPostCreate(_PostFields):
    model_config = ConfigDict(json_schema_extra={"example": {
        "kind": "blog", "title": "Five stretches for desk workers", "excerpt": "Short, easy moves for the working day.",
        "content": "<p>Start with your neck…</p>", "category_id": "5f0c0a52-6a0c-4f0e-9a52-0b7d0e2a9c11",
        "cover_image_url": "/api/v1/uploads/blogs/stretch.jpg", "tags": ["wellness", "office"]}})

    title: str = Field(..., min_length=1, max_length=TITLE_MAX)
    enterprise_id: UUID | None = Field(None, description="Only when the business has more than one; defaults to its first")


class MerchantPostUpdate(_PostFields):
    enterprise_id: UUID | None = None


class AdminPostCreate(_PostFields):
    model_config = ConfigDict(json_schema_extra={"example": {
        "kind": "news", "title": "Platform maintenance on Sunday", "excerpt": "Short downtime planned.",
        "content": "<p>We will be offline from 2 to 3 am.</p>", "category_id": "5f0c0a52-6a0c-4f0e-9a52-0b7d0e2a9c11",
        "status": "published"}})

    title: str = Field(..., min_length=1, max_length=TITLE_MAX)
    slug: str | None = Field(None, max_length=255, description="URL slug; made from the title when omitted")
    author_name: str | None = Field(None, max_length=255, description="Shown as the author; defaults to the platform name")
    status: Literal["draft", "published"] = Field("draft", description="published makes it live straight away")
    tenant_id: UUID | None = None
    enterprise_id: UUID | None = None


class AdminPostUpdate(_PostFields):
    slug: str | None = Field(None, max_length=255)
    author_name: str | None = Field(None, max_length=255)


class BlogStatusUpdate(BaseModel):
    status: BlogStatusInput
    reason: str | None = Field(None, max_length=500)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"action": "request_changes", "reason": "Please add a source for the claim in paragraph 2."}})

    action: ReviewAction
    reason: str | None = Field(None, max_length=500, description="Required for request_changes and reject; shown to the merchant")


class ReasonRequest(BaseModel):
    reason: str | None = Field(None, max_length=500, description="Required when a Super Admin acts on a merchant's post")


class PinRequest(BaseModel):
    pinned: bool = True


class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    is_active: bool = True


class CategoryUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    is_active: bool | None = None


class CategoryReorder(BaseModel):
    ids: list[UUID] = Field(..., min_length=1, description="Every category id, in the order they should appear")


# ---------------------------------------------------------------- responses

class BlogCategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    is_active: bool = True
    sort_order: int = 0
    post_count: int | None = Field(None, description="Live posts in the category (public list) or all non-deleted posts (admin list)")


class BlogCategoryListResponse(BaseModel):
    items: list[BlogCategoryResponse]
    total: int


class BlogListItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    slug: str
    kind: BlogKind = "blog"
    excerpt: str | None = None
    cover_image_url: str | None = None
    author_name: str | None = None
    author_type: BlogSource = Field("platform", description="platform | merchant. For a merchant post, business_name and business_logo_url are the author")
    business_name: str | None = None
    business_logo_url: str | None = None
    tenant_id: UUID | None = None
    enterprise_id: UUID | None = None
    category: str | None = None
    category_id: UUID | None = None
    tags: list[str] | None = None
    is_pinned: bool = False
    status: BlogStatus
    published_at: datetime | None = None
    view_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BlogDetailResponse(BlogListItemResponse):
    content: str
    author_id: UUID | None = None
    created_by: str | None = None
    updated_by: str | None = None


class BlogPaginatedResponse(PaginatedResponse[BlogListItemResponse]):
    pass


class BlogLiveVersion(BaseModel):
    """What the public sees while the merchant's edit waits for approval."""
    kind: BlogKind
    title: str
    excerpt: str | None = None
    content: str
    cover_image_url: str | None = None
    category: str | None = None
    category_id: UUID | None = None
    tags: list[str] | None = None


class BlogManageItem(BlogListItemResponse):
    status_label: str
    is_live: bool = Field(False, description="The public can read the post now. True for a published post even while an edit waits for approval")
    has_pending_revision: bool = False
    reviewer_note: str | None = Field(None, description="The Super Admin's reason (changes requested, rejected, or taken down)")
    reviewed_at: datetime | None = None
    submitted_at: datetime | None = None
    actions: list[str] = Field(default_factory=list, description="What the caller may do with this post now: edit, submit, unpublish, delete, publish, review, pin, unpin")


class BlogManageDetail(BlogManageItem):
    content: str
    author_id: UUID | None = None
    created_by: str | None = None
    updated_by: str | None = None
    live_version: BlogLiveVersion | None = Field(None, description="Only while an edit of a live post waits for approval; the top-level fields are then the edit")


class BlogManagePaginatedResponse(PaginatedResponse[BlogManageItem]):
    pass


class BlogHistoryEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    action: str
    from_status: str | None = None
    to_status: str | None = None
    reason: str | None = None
    actor_id: str | None = None
    actor_name: str | None = None
    actor_role: str | None = None
    created_at: datetime


class BlogHistoryResponse(BaseModel):
    items: list[BlogHistoryEntry]
    total: int
