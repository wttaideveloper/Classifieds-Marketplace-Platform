from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common_schema import PaginatedResponse

FormType = Literal["blog", "news", "both"]
FormMode = Literal["standard", "configurable"]
FormScope = Literal["global", "selected"]

EXAMPLE_DEFINITION = {"sections": [
    {"key": "basics", "title": "Basics", "fields": [
        {"key": "title", "required": True, "help_text": "Keep it under 80 characters", "validation": {"max_length": 80}},
        {"key": "category"}, {"key": "excerpt", "required": True}]},
    {"key": "content", "title": "Content", "fields": [{"key": "content", "validation": {"min_length": 200}}]},
    {"key": "details", "title": "Details", "fields": [
        {"key": "event_date", "type": "date", "label": "Event date", "visibility": "public"},
        {"key": "audience", "type": "select", "label": "Audience", "required": True, "options": [{"value": "kids", "label": "Kids"}, {"value": "adults", "label": "Adults"}]},
        {"key": "internal_note", "type": "textarea", "label": "Note for the editors", "visibility": "internal", "enterprise_only": True}]},
    {"key": "media", "title": "Images", "fields": [{"key": "cover_image", "required": True}, {"key": "additional_images"}]},
]}


class FormCreate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "name": "News with event details", "description": "For news that is about an event", "form_type": "news", "mode": "configurable",
        "scope": "selected", "tenant_ids": ["0b8f3a06-6f43-4b7b-9d4a-1c2a1e0b5a11"], "definition": EXAMPLE_DEFINITION}})

    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = Field(None, max_length=500)
    form_type: FormType = "both"
    mode: FormMode = Field("standard", description="standard = the built-in fields, which cannot be changed. configurable = you set the fields")
    scope: FormScope = Field("global", description="global = every business. selected = only the businesses in tenant_ids")
    tenant_ids: list[UUID] | None = Field(None, description="The businesses (by tenant id) for scope=selected")
    definition: dict | None = Field(None, description="Sections and fields. Needed for mode=configurable; ignored for standard")


class FormUpdate(BaseModel):
    """Saves the draft only. Publish the form to make the change reach posts."""
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=500)
    form_type: FormType | None = None
    mode: FormMode | None = None
    scope: FormScope | None = None
    tenant_ids: list[UUID] | None = None
    definition: dict | None = None
    base_revision: int | None = Field(None, description="The `revision` you loaded. If someone saved since, the answer is 409 VERSION_CONFLICT")


class FormAssign(BaseModel):
    scope: FormScope
    tenant_ids: list[UUID] | None = None


class FormPublish(BaseModel):
    note: str | None = Field(None, max_length=500, description="What changed in this version")
    base_revision: int | None = None


class FormPreviewRequest(BaseModel):
    name: str | None = Field(None, max_length=120)
    mode: FormMode | None = None
    definition: dict | None = None


class FormAssignment(BaseModel):
    tenant_id: UUID
    business_name: str | None = None


class FormItem(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    form_type: FormType
    mode: FormMode
    scope: FormScope
    status: Literal["draft", "published", "retired"]
    lifecycle: Literal["draft", "in_review", "inactive", "active", "retired"] = Field("draft", description="The same words for every module: draft, in_review (submitted), inactive (published, not offered), active, retired")
    available_actions: list[str] = Field(default_factory=list, description="What the caller may do with the form now: edit, review, submit, publish, activate, deactivate, retire, restore, assign, delete")
    is_active: bool
    current_version: int = Field(..., description="The latest published version; 0 while the form has never been published")
    revision: int = Field(..., description="Goes up with every save of the draft. Send it back as base_revision")
    has_unpublished_changes: bool
    review_status: Literal["draft", "submitted"] = Field("draft", description="submitted = reviewed and ready to publish, with no edit since")
    assigned_count: int = 0
    post_count: int = Field(0, description="Posts written with this form (any version)")
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FormDetail(FormItem):
    assignments: list[FormAssignment] = Field(default_factory=list)
    definition: dict[str, Any] = Field(..., description="The draft definition")


class FormPage(PaginatedResponse[FormItem]):
    pass


class FormVersionItem(BaseModel):
    version: int
    name: str
    form_type: FormType
    mode: FormMode
    note: str | None = None
    published_by: str | None = None
    published_at: datetime
    post_count: int = 0


class FormVersionList(BaseModel):
    items: list[FormVersionItem]
    total: int


class FormVersionDetail(BaseModel):
    form_id: UUID
    version: int
    name: str
    form_type: FormType
    mode: FormMode
    note: str | None = None
    published_by: str | None = None
    published_at: datetime
    is_current: bool
    definition: dict[str, Any]


class FormPreview(BaseModel):
    source: Literal["draft", "unsaved"]
    name: str
    mode: FormMode
    definition: dict[str, Any] = Field(..., description="Every field, as the Super Admin's builder sees it")
    field_count: int
    required_fields: list[str]
    enterprise_admin_view: dict[str, Any] | None = Field(None, description="The form as an Enterprise Admin would be given it: enabled fields only, with Enterprise Admin only fields")
    public_view: dict[str, Any] | None = Field(None, description="What the public and mobile apps would know about it: enabled published fields only")


class ResolvedFormOut(BaseModel):
    """The form a post is written with. form_id is null and version 0 for the built-in Standard form."""
    form_id: UUID | None = None
    version: int = 0
    name: str
    mode: FormMode
    form_type: FormType
    definition: dict[str, Any]


class ActiveFormsForBusiness(BaseModel):
    tenant_id: UUID | None = None
    business_name: str | None = None
    blog: ResolvedFormOut
    news: ResolvedFormOut


class SectionCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    description: str | None = Field(None, max_length=300)
    visibility: Literal["published", "enterprise_admin_only"] = Field("published", description="enterprise_admin_only: the section and everything in it is for Enterprise Admins only and never reaches the public APIs")
    position: int | None = Field(None, ge=1, description="1-based place among the sections; at the end when left out")
    base_revision: int | None = Field(None, description="The form's `revision` you loaded; a newer one is 409 VERSION_CONFLICT")


class SectionEdit(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=300)
    visibility: Literal["published", "enterprise_admin_only"] | None = None
    enabled: bool | None = Field(None, description="false switches the whole section off")
    base_revision: int | None = None


class OrderBody(BaseModel):
    ids: list[str] = Field(..., min_length=1, description="Every id of the sections (or of the section's fields) once, in the new order")
    base_revision: int | None = None


class FieldCreate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "section_id": "7d0c…", "position": 2, "field": {"key": "event_date", "label": "Event date", "type": "date", "required": True, "help_text": "When it happens",
                                                          "validation": {"not_in_past": True}, "visibility": "published"}}})

    section_id: str = Field(..., description="The section's id (its key also works)")
    position: int | None = Field(None, ge=1, description="1-based place in the section; at the end when left out")
    field: dict[str, Any] = Field(..., description="key, label, type and optionally placeholder, help_text, required, enabled, validation, options, visibility. Types: "
                                                   "short_text, rich_text, long_text, dropdown, radio, checkbox, number, date, time, reference_url, additional_images, "
                                                   "supporting_document, video, tags")
    base_revision: int | None = None


class FieldEdit(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"patch": {"label": "Main speaker", "required": True, "validation": {"max_length": 60}}}})

    patch: dict[str, Any] = Field(..., description="Only what changes. `key` and `type` can never change; send them only if they equal the stored ones")
    base_revision: int | None = None


class FieldMove(BaseModel):
    section_id: str
    position: int | None = Field(None, ge=1)
    base_revision: int | None = None


class BuilderChange(BaseModel):
    action: str
    summary: str


class FormChange(FormDetail):
    """The updated draft, and what just changed (`change.field_id` is the new field after an add or a duplicate)."""
    change: dict[str, Any]


class FormReview(BaseModel):
    form_id: UUID
    revision: int
    ready: bool = Field(..., description="Nothing stops this draft from being published")
    problems: list[dict[str, Any]] = Field(default_factory=list, description="{field, code, message} for each thing to fix")
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    changes: dict[str, Any] = Field(..., description="Compared with the published version: fields added, removed, and changed (which settings)")
    section_count: int
    field_count: int
    review_status: Literal["draft", "submitted"]
    review_required_to_publish: bool


class AuditItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    form_id: UUID
    form_name: str | None = None
    action: str
    summary: str
    version: int | None = None
    revision: int | None = None
    detail: dict[str, Any] | None = None
    actor_id: str | None = None
    actor_name: str | None = None
    actor_role: str | None = None
    created_at: datetime


class AuditPage(PaginatedResponse[AuditItem]):
    pass


class FieldTypeInfo(BaseModel):
    type: str
    renderer: str
    label: str
    supports_options: bool
    accepts_files: list[str] = Field(default_factory=list, description="The file types the field can be limited to")
    rules: dict[str, Any] = Field(default_factory=dict, description="The validation rules this type accepts, each with its kind and bounds")
