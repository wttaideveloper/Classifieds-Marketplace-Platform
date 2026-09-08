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
    composite_config: bool = False


class FieldOption(BaseModel):
    value: str
    label: str
    position: int = 1


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
    model_config = ConfigDict(extra="allow")
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
    options: list[FieldOption | dict] = Field(default_factory=list)
    validation: FieldValidation | dict = Field(default_factory=dict)
    composite_config: dict | None = None


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
    options: list[dict] = Field(default_factory=list)
    validation: dict = Field(default_factory=dict)
    composite_config: dict | None = None


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
