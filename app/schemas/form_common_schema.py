from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

Audience = Literal["public", "enterprise_admin", "super_admin"]


class CommonFormView(BaseModel):
    """A form in the common contract. `definition.sections[].fields[]` have id, key, label, type, renderer, placeholder, help_text, required, enabled, order, validation, options, visibility (and system,
    kind, core_key, composite_config, module_type where they apply, depending on the audience)."""
    configuration_id: UUID | None = None
    name: str | None = None
    scope: str | None = None
    status: str | None = Field(None, description="The configuration's status (builder view only)")
    is_active: bool | None = None
    version_id: UUID | None = None
    version: int | None = None
    version_status: str | None = Field(None, description="draft or published (builder view only)")
    audience: Audience
    definition: dict[str, Any]
    field_count: int | None = None
    etag: str | None = Field(None, description="A hash of this version's content (builder view), also the `ETag` header. Send it back as `base_etag` or `If-Match` to refuse a change over someone else's")
    lifecycle: Literal["draft", "in_review", "inactive", "active", "retired"] | None = Field(None, description="The same words for every module")
    available_actions: list[str] | None = None
    description: str | None = Field(None, description="The form's description (builder view)")
    assignments: list[dict[str, Any]] | None = Field(None, description="A selective form's businesses: [{tenant_id, enterprise_id, business_name}] (builder view)")
    published_at: datetime | None = Field(None, description="When the version was published (public view)")
    review_status: Literal["draft", "submitted"] | None = Field(None, description="submitted = reviewed and ready to publish, with no edit since (builder view)")


class RecordFormView(BaseModel):
    event_id: UUID | None = None
    training_id: UUID | None = None
    audience: Audience = Field(..., description="Who the answer was prepared for: decided by the server from who asks")
    configuration_id: UUID | None = None
    version_id: UUID | None = None
    version: int | None = None
    definition: dict[str, Any]
    custom_fields: list[dict[str, Any]] = Field(default_factory=list, description="[{key, label, type, renderer, value}] in form order; `visibility` too for staff")


class CommonFormChange(CommonFormView):
    change: dict[str, Any] = Field(..., description="What just changed: action, summary and, for a new or copied field, `field_id`")


class CommonSectionCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    description: str | None = Field(None, max_length=300)
    visibility: Literal["published", "enterprise_admin_only"] = Field("published", description="enterprise_admin_only: the section and everything in it is for Enterprise Admins only")
    position: int | None = Field(None, ge=1)
    base_etag: str | None = None


class CommonSectionEdit(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=300)
    visibility: Literal["published", "enterprise_admin_only"] | None = None
    enabled: bool | None = Field(None, description="false switches the whole section off")
    base_etag: str | None = None


class CommonOrder(BaseModel):
    ids: list[str] = Field(..., min_length=1)
    base_etag: str | None = None


class CommonFieldCreate(BaseModel):
    section_id: str
    position: int | None = Field(None, ge=1)
    field: dict[str, Any] = Field(..., description="key, label, type (short_text, rich_text, long_text, dropdown, radio, checkbox, number, date, time, reference_url, additional_images, "
                                                   "supporting_document, video, tags, datetime, toggle) and optionally placeholder, help_text, required, enabled, validation, options, visibility")
    base_etag: str | None = None


class CommonFieldEdit(BaseModel):
    patch: dict[str, Any] = Field(..., description="Only what changes. For a built-in field only label, placeholder, help_text, required and enabled")
    base_etag: str | None = None


class CommonFieldMove(BaseModel):
    section_id: str
    position: int | None = Field(None, ge=1)
    base_etag: str | None = None


class CommonSubmit(BaseModel):
    base_etag: str | None = None


class CommonReview(BaseModel):
    configuration_id: UUID
    version_id: UUID
    version: int
    etag: str
    ready: bool
    problems: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    changes: dict[str, Any]
    section_count: int
    field_count: int
    review_status: Literal["draft", "submitted"]
    review_required_to_publish: bool
