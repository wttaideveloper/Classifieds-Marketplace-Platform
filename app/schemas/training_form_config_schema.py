from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FieldConfigurableFlags(BaseModel):
    label: bool = True
    section: bool = True
    position: bool = True
    required: bool = True
    renderer: bool = True
    placeholder: bool = True
    help_text: bool = True
    validation: bool = True
    composite_config: bool = True


class FieldOption(BaseModel):
    value: str
    label: str
    position: int = 1
    parent_value: str | None = Field(
        None,
        description="For a subcategory field's options: the parent category option's value this belongs to. "
                    "Enterprise Admin's subcategory dropdown should filter to options whose parent_value matches "
                    "the selected category; the server also enforces this pairing on submit.",
    )


class FieldValidation(BaseModel):
    min_length: int | None = None
    max_length: int | None = None
    min: float | None = None
    max: float | None = None
    pattern: str | None = None


class FieldRegistryEntry(BaseModel):
    key: str
    display_name: str
    value_type: str
    allowed_renderers: list[str]
    default_renderer: str
    required_by_domain: bool
    removable: bool
    hideable: bool
    configurable: FieldConfigurableFlags
    supports_composite_config: bool = False
    composite_subfields: list = Field(default_factory=list)
    default_composite_config: dict | None = None


class FormFieldInput(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "example": {
                "id": "field_subcategory",
                "source": "core",
                "core_key": "subcategory",
                "label": "Subcategory",
                "renderer": "select",
                "required": False,
                "is_enabled": True,
                "options": [
                    {"value": "yoga", "label": "Yoga", "position": 1, "parent_value": "wellness"},
                    {"value": "nutrition", "label": "Nutrition", "position": 2, "parent_value": "wellness"},
                ],
                "composite_config": {"frontend_settings": {"allow_custom_value": True}},
            }
        },
    )
    id: str | None = None
    source: str = Field(..., description="core|custom")
    core_key: str | None = None
    stable_key: str | None = None
    label: str
    renderer: str
    value_type: str | None = None
    required: bool = False
    is_enabled: bool = True
    position: int = 1
    placeholder: str | None = None
    help_text: str | None = None
    options: list[FieldOption | dict] = Field(
        default_factory=list,
        description="Dropdown choices for renderer='select'/'multi_select'. For the subcategory core field, "
                    "each option may set parent_value to a category option's value to scope it under that category.",
    )
    validation: FieldValidation | dict = Field(default_factory=dict)
    composite_config: dict | None = Field(
        None,
        description="Open JSON metadata, preserved verbatim. frontend_settings.visibility, frontend_settings.upload "
                    "and frontend_settings.allow_custom_value are validated and enforced server-side. "
                    "allow_custom_value: true lets a submitted value bypass the options allowlist (an 'Other' free-text "
                    "entry) — submit it as the plain category/subcategory string, no separate wrapper or flag needed. "
                    "A custom subcategory value (one not found in options) is not checked against parent_value.",
    )


class FormSectionInput(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str | None = None
    stable_key: str
    label: str
    description: str | None = None
    position: int = 1
    is_enabled: bool = True
    fields: list[FormFieldInput] = Field(default_factory=list)


class FormFieldResponse(BaseModel):
    id: str
    source: str
    core_key: str | None = None
    stable_key: str | None = None
    label: str
    renderer: str
    value_type: str
    required: bool
    is_enabled: bool
    position: int
    placeholder: str | None = None
    help_text: str | None = None
    options: list[dict] = Field(
        default_factory=list,
        description="Dropdown choices. For the subcategory core field, an option's parent_value (if set) names the "
                    "category option it belongs to — filter the subcategory dropdown to the selected category's value.",
    )
    validation: dict = Field(default_factory=dict)
    composite_config: dict | None = Field(
        None,
        description="Open JSON metadata, preserved verbatim. frontend_settings.visibility, frontend_settings.upload "
                    "and frontend_settings.allow_custom_value are validated and enforced server-side. "
                    "allow_custom_value: true means the field accepts an 'Other' free-text value outside options — "
                    "render an 'Other' choice and submit whatever the admin types as the plain field value.",
    )


class FormSectionResponse(BaseModel):
    id: str
    stable_key: str
    label: str
    description: str | None = None
    position: int
    is_enabled: bool = True
    fields: list[FormFieldResponse] = Field(default_factory=list)


class ConfigurationVersionResponse(BaseModel):
    id: UUID
    configuration_id: UUID
    version: int
    status: str
    sections: list[FormSectionResponse]
    created_by: str | None = None
    created_at: datetime | None = None
    published_at: datetime | None = None


class TrainingFormConfigurationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    scope: str = Field("global", description="global|selective")
    sections: list[FormSectionInput] = Field(default_factory=list)


class TrainingFormConfigurationUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    sections: list[FormSectionInput] | None = None


class TrainingFormConfigurationSummary(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    scope: str
    is_global: bool = Field(False, description="True when scope=global")
    status: str
    is_active: bool
    current_version: int
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    published_at: datetime | None = None


class TrainingFormConfigurationDetail(TrainingFormConfigurationSummary):
    draft_version: ConfigurationVersionResponse | None = None
    published_version: ConfigurationVersionResponse | None = None


class TrainingFormConfigurationCreateResponse(TrainingFormConfigurationSummary):
    draft_version: ConfigurationVersionResponse


class PublishConfigurationResponse(BaseModel):
    sections: list[FormSectionResponse] = Field(default_factory=list)
    configuration_id: UUID
    version_id: UUID
    version: int
    status: str
    published_at: datetime


class ActivationResponse(BaseModel):
    id: UUID
    is_active: bool
    status: str


class AssignmentItem(BaseModel):
    tenant_id: UUID
    enterprise_id: UUID | None = None


class AssignmentPutRequest(BaseModel):
    tenant_ids: list[UUID] | None = Field(None, description="Canonical tenant UUIDs")
    tenant_slugs: list[str] | None = None
    enterprise_ids: list[UUID] | None = Field(
        None,
        description="Empty list [] converts config to global; non-empty makes selective assignments",
    )
    assignments: list[AssignmentItem] | None = None
    is_global: bool | None = Field(
        None,
        description="Explicit true also converts config to global (alias for enterprise_ids: []).",
    )


class AssignmentResponse(BaseModel):
    configuration_id: UUID
    scope: str | None = None
    is_global: bool | None = None
    assignments: list[AssignmentItem]


class ActiveFormConfigurationResponse(BaseModel):
    configuration_id: UUID
    version_id: UUID
    name: str
    scope: str
    is_global: bool = False
    version: int
    sections: list[FormSectionResponse]
    configuration_version: str | None = None


class TrainingFormAuditEntry(BaseModel):
    id: UUID
    configuration_id: UUID | None = None
    version_id: UUID | None = None
    actor_id: str | None = None
    action: str
    before: dict | None = None
    after: dict | None = None
    created_at: datetime | None = None
