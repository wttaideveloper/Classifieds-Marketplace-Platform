from datetime import datetime
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings
from app.schemas.common_schema import PaginatedResponse

BlogKind = Literal["blog", "news"]
BlogSource = Literal["platform", "merchant"]
BlogModule = Literal["events", "trainings", "products", "services", "general"]
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


def _web_or_path(value: str | None, *, allow_path: bool) -> str:
    url = (value or "").strip()
    parsed = urlparse(url)
    if not url or len(url) > 2000 or url.startswith("//"):
        raise ValueError("must be an address")
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return url
    if allow_path and parsed.scheme == "" and url.startswith("/"):
        return url
    raise ValueError("must be an http(s) address" + (" or a path on this site" if allow_path else ""))


class ImageItem(BaseModel):
    url: str = Field(..., description="Address of the picture; for an uploaded one, the `url` that POST /uploads returned")
    alt: str | None = Field(None, max_length=200, description="Text for screen readers")
    caption: str | None = Field(None, max_length=300)

    @field_validator("url")
    @classmethod
    def _url(cls, value):
        return _web_or_path(value, allow_path=True)


class ReferenceLink(BaseModel):
    url: str = Field(..., description="An http(s) address")
    label: str | None = Field(None, max_length=150)

    @field_validator("url")
    @classmethod
    def _url(cls, value):
        return _web_or_path(value, allow_path=False)


class DocumentItem(BaseModel):
    url: str = Field(..., description="The `url` that POST /uploads returned for a document")
    name: str | None = Field(None, max_length=255)
    size: int | None = Field(None, ge=0)
    content_type: str | None = Field(None, max_length=100)

    @field_validator("url")
    @classmethod
    def _url(cls, value):
        return _web_or_path(value, allow_path=True)


class _PostFields(BaseModel):
    kind: BlogKind | None = Field(None, description="blog | news. Decides which list the post shows in")
    title: str | None = Field(None, max_length=TITLE_MAX)
    excerpt: str | None = Field(None, max_length=SUMMARY_MAX, description="The short summary shown on the list card")
    content: str | None = Field(None, max_length=BODY_MAX, description="The body: formatted text (headings, bold, italic, lists, links, images). Anything else is removed")
    cover_image_url: str | None = Field(None, description="One cover image (upload it with POST /uploads first)")
    category_id: UUID | None = Field(None, description="An active category from GET /cms/blogs/categories")
    category: str | None = Field(None, max_length=100, description="Alternative to category_id: the name of an active category")
    tags: list[str] | None = Field(None, description="Optional, up to 10 short tags")
    additional_images: list[ImageItem] | None = Field(None, description="More pictures for the post. Send the whole list; it replaces the old one")
    reference_urls: list[ReferenceLink] | None = Field(None, description="Links to sources. Send the whole list")
    supporting_documents: list[DocumentItem] | None = Field(None, description="Uploaded documents. Send the whole list")
    custom_fields: dict[str, Any] | None = Field(None, description="Values of the form's custom fields, by field key. Only the keys sent are changed; send null or an empty value to clear one")

    @field_validator("additional_images")
    @classmethod
    def _more_images(cls, value):
        if value is not None and len(value) > settings.BLOG_MAX_ADDITIONAL_IMAGES:
            raise ValueError(f"at most {settings.BLOG_MAX_ADDITIONAL_IMAGES} additional images")
        return value

    @field_validator("reference_urls")
    @classmethod
    def _more_links(cls, value):
        if value is not None and len(value) > settings.BLOG_MAX_REFERENCE_URLS:
            raise ValueError(f"at most {settings.BLOG_MAX_REFERENCE_URLS} reference links")
        return value

    @field_validator("supporting_documents")
    @classmethod
    def _more_documents(cls, value):
        if value is not None and len(value) > settings.BLOG_MAX_DOCUMENTS:
            raise ValueError(f"at most {settings.BLOG_MAX_DOCUMENTS} documents")
        return value

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
    module: BlogModule = Field("general", description="The module the post is about. Refused (409 MODULE_DISABLED) while Blog governance has it switched off")
    enterprise_id: UUID | None = Field(None, description="Only when the business has more than one; defaults to its first")


class MerchantPostUpdate(_PostFields):
    enterprise_id: UUID | None = None
    base_version: int | None = Field(None, description="The `version` you loaded. If the post changed since, the answer is 409 VERSION_CONFLICT. The If-Match header does the same")


class AdminPostCreate(_PostFields):
    model_config = ConfigDict(json_schema_extra={"example": {
        "kind": "news", "title": "Platform maintenance on Sunday", "excerpt": "Short downtime planned.",
        "content": "<p>We will be offline from 2 to 3 am.</p>", "category_id": "5f0c0a52-6a0c-4f0e-9a52-0b7d0e2a9c11",
        "status": "published"}})

    title: str = Field(..., min_length=1, max_length=TITLE_MAX)
    module: BlogModule = Field("general", description="The module the post is about. Refused (409 MODULE_DISABLED) while Blog governance has it switched off")
    slug: str | None = Field(None, max_length=255, description="URL slug; made from the title when omitted")
    author_name: str | None = Field(None, max_length=255, description="Shown as the author; defaults to the platform name")
    status: Literal["draft", "published"] = Field("draft", description="published makes it live straight away")
    tenant_id: UUID | None = None
    enterprise_id: UUID | None = None


class AdminPostUpdate(_PostFields):
    slug: str | None = Field(None, max_length=255)
    author_name: str | None = Field(None, max_length=255)
    base_version: int | None = Field(None, description="The `version` you loaded (or send If-Match)")


class BlogStatusUpdate(BaseModel):
    status: BlogStatusInput
    reason: str | None = Field(None, max_length=500)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"action": "request_changes", "reason": "Please add a source for the claim in paragraph 2."}})

    action: ReviewAction
    reason: str | None = Field(None, max_length=500, description="Required for request_changes and reject; shown to the merchant")
    base_version: int | None = Field(None, description="The `version` you reviewed (or send If-Match), so a decision is not made on a post that changed meanwhile")


class ReasonRequest(BaseModel):
    reason: str | None = Field(None, max_length=500, description="Required when a Super Admin acts on a merchant's post")
    base_version: int | None = None


class PinRequest(BaseModel):
    pinned: bool = True
    base_version: int | None = None


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
    module: BlogModule = "general"
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


class PublicCustomField(BaseModel):
    key: str
    label: str
    type: str = Field(..., description="The explicit field type, for example short_text, dropdown, date, video, additional_images")
    renderer: str | None = Field(None, description="Which control draws it, for example text_input, select, image_uploader")
    value: Any


class BlogDetailResponse(BlogListItemResponse):
    content: str
    author_id: UUID | None = None
    created_by: str | None = None
    updated_by: str | None = None
    additional_images: list[ImageItem] = Field(default_factory=list)
    reference_urls: list[ReferenceLink] = Field(default_factory=list)
    supporting_documents: list[DocumentItem] = Field(default_factory=list)
    custom_fields: list[PublicCustomField] = Field(default_factory=list, description="Only the fields the form marks public, with their labels")


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
    additional_images: list[ImageItem] = Field(default_factory=list)
    reference_urls: list[ReferenceLink] = Field(default_factory=list)
    supporting_documents: list[DocumentItem] = Field(default_factory=list)
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class BlogFormRef(BaseModel):
    """The form a post is written with. form_id is null and version 0 for the built-in Standard form."""
    form_id: UUID | None = None
    version: int = 0
    name: str
    mode: str
    form_type: str
    definition: dict[str, Any]


class PendingRevisionInfo(BaseModel):
    submitted_at: datetime | None = None
    changed_fields: list[str] = Field(default_factory=list)
    status: str = Field(..., description="The status the post is in because of the edit: pending_approval, changes_requested or rejected")


class BlogManageItem(BlogListItemResponse):
    status_label: str
    is_live: bool = Field(False, description="The public can read the post now. True for a published post even while an edit waits for approval")
    has_pending_revision: bool = False
    reviewer_note: str | None = Field(None, description="The Super Admin's reason (changes requested, rejected, or taken down)")
    reviewed_at: datetime | None = None
    submitted_at: datetime | None = None
    actions: list[str] = Field(default_factory=list, description="What the caller may do with this post now: edit, submit, unpublish, delete, publish, review, pin, unpin, discard_changes")
    version: int = Field(1, description="Goes up with every change to the post. Send it back as base_version or If-Match; the ETag header carries it too")
    form_id: UUID | None = None
    form_version: int | None = Field(None, description="The version of the form the post was written with; null for the built-in Standard form")


class BlogManageDetail(BlogManageItem):
    content: str
    author_id: UUID | None = None
    created_by: str | None = None
    updated_by: str | None = None
    live_version: BlogLiveVersion | None = Field(None, description="Only while an edit of a live post waits for approval; the top-level fields are then the edit")
    pending_revision: PendingRevisionInfo | None = Field(None, description="Present while an edit of a live post is waiting or was sent back")
    additional_images: list[ImageItem] = Field(default_factory=list)
    reference_urls: list[ReferenceLink] = Field(default_factory=list)
    supporting_documents: list[DocumentItem] = Field(default_factory=list)
    custom_fields: dict[str, Any] = Field(default_factory=dict, description="Values by field key, for the form version below")
    form: BlogFormRef | None = Field(None, description="The form (with its fields) this post is written with. A post that is not live follows the active form")


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
    post_version: int | None = Field(None, description="The post's `version` after this change")
    created_at: datetime


class BlogHistoryResponse(BaseModel):
    items: list[BlogHistoryEntry]
    total: int


class BlogRevisionItem(BaseModel):
    id: UUID
    action: str
    post_version: int | None = None
    title: str | None = None
    form_version: int | None = None
    from_status: str | None = None
    to_status: str | None = None
    reason: str | None = None
    actor_id: str | None = None
    actor_name: str | None = None
    actor_role: str | None = None
    created_at: datetime


class BlogRevisionList(BaseModel):
    items: list[BlogRevisionItem]
    total: int


class BlogRevisionDetail(BlogRevisionItem):
    snapshot: dict[str, Any] = Field(..., description="The content of the post after this change, in the shape of the post's own fields")


class BlogPreviewResponse(BaseModel):
    """How the post would look to a reader, plus whether it could be sent now."""
    preview: bool = True
    id: UUID | None = None
    slug: str | None = None
    status: str = "draft"
    kind: BlogKind = "blog"
    title: str = ""
    excerpt: str | None = None
    content: str = ""
    cover_image_url: str | None = None
    category: str | None = None
    category_id: UUID | None = None
    tags: list[str] = Field(default_factory=list)
    author_name: str | None = None
    author_type: BlogSource = "platform"
    business_name: str | None = None
    business_logo_url: str | None = None
    additional_images: list[ImageItem] = Field(default_factory=list)
    reference_urls: list[ReferenceLink] = Field(default_factory=list)
    supporting_documents: list[DocumentItem] = Field(default_factory=list)
    custom_fields: list[PublicCustomField] = Field(default_factory=list)
    form: BlogFormRef | None = None
    ready_to_submit: bool = Field(..., description="True when nothing required is missing")
    problems: list[dict[str, Any]] = Field(default_factory=list, description="What is missing or wrong: {field, code, message}")


class PreviewRequest(_PostFields):
    """An unsaved post to preview. Nothing is stored."""
    title: str | None = Field(None, max_length=TITLE_MAX)
    enterprise_id: UUID | None = None


class FileUploadResponse(BaseModel):
    file_id: UUID
    url: str = Field(..., description="Put this in cover_image_url, additional_images[].url or supporting_documents[].url")
    name: str | None = None
    size: int
    content_type: str
    purpose: Literal["cover_image", "image", "document", "video"]
    width: int | None = None
    height: int | None = None
    scan_status: str
    created_at: datetime


class FileUsage(BaseModel):
    post_id: UUID
    title: str
    status: str
    is_live: bool


class FileDetail(FileUploadResponse):
    sha256: str | None = None
    replaced_at: datetime | None = Field(None, description="When its content was last replaced; the address stays the same")
    used_by: list[FileUsage] = Field(default_factory=list, description="Every post that still uses the file, in any state")


class FilePage(PaginatedResponse[FileUploadResponse]):
    pass


class SignedUrl(BaseModel):
    url: str = Field(..., description="Works without signing in until `expires_at`. Anyone holding it can read the file")
    expires_at: datetime


class UploadLimits(BaseModel):
    image_max_mb: int
    document_max_mb: int
    video_max_mb: int = 0
    video_extensions: list[str] = Field(default_factory=list)
    signed_url_seconds: int = 0
    image_types: list[str]
    document_extensions: list[str]
    max_additional_images: int
    max_documents: int
    max_reference_urls: int
