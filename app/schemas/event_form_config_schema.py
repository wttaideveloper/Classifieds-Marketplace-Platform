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


class EventFormConfigurationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    scope: str = Field("global", description="global|selective")
    sections: list[FormSectionInput] = Field(default_factory=list)


class EventFormConfigurationUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    sections: list[FormSectionInput] | None = None


class EventFormConfigurationSummary(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    scope: str
    status: str
    is_active: bool
    current_version: int
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    published_at: datetime | None = None


class EventFormConfigurationDetail(EventFormConfigurationSummary):
    draft_version: ConfigurationVersionResponse | None = None
    published_version: ConfigurationVersionResponse | None = None


class EventFormConfigurationCreateResponse(EventFormConfigurationSummary):
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
    tenant_ids: list[UUID] | None = None
    enterprise_ids: list[UUID] | None = None
    assignments: list[AssignmentItem] | None = None


class AssignmentResponse(BaseModel):
    configuration_id: UUID
    assignments: list[AssignmentItem]


class ActiveFormConfigurationResponse(BaseModel):
    configuration_id: UUID
    version_id: UUID
    name: str
    scope: str
    version: int
    sections: list[FormSectionResponse]
    configuration_version: str | None = Field(None, description="Version header for caching — format: {config_id}:{version}")


class EventFormAuditEntry(BaseModel):
    id: UUID
    configuration_id: UUID | None = None
    version_id: UUID | None = None
    actor_id: str | None = None
    action: str
    before: dict | None = None
    after: dict | None = None
    created_at: datetime


class EventCustomValueInput(BaseModel):
    field_id: str = Field(..., description="Custom field instance ID from form configuration")
    value: str | int | float | bool | list | dict | None = None


class EventCustomValueResponse(BaseModel):
    field_id: str
    value: str | int | float | bool | list | dict | None = None
