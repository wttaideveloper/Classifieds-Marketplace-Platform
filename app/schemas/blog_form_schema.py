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
    status: Literal["draft", "published"]
    is_active: bool
    current_version: int = Field(..., description="The latest published version; 0 while the form has never been published")
    revision: int = Field(..., description="Goes up with every save of the draft. Send it back as base_revision")
    has_unpublished_changes: bool
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
    definition: dict[str, Any]
    field_count: int
    required_fields: list[str]


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
