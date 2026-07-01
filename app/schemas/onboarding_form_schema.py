from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common_schema import PaginatedResponse

OnboardingFormStatus = Literal["draft", "published", "inactive"]
OnboardingFormEntityType = Literal["enterprise"]
OnboardingFormRegistrationType = Literal["enterprise", "individual"]
OnboardingFieldType = Literal[
    "text",
    "textarea",
    "email",
    "phone",
    "number",
    "url",
    "date",
    "dropdown",
    "checkbox",
    "radio",
    "file",
    "image",
]

OPTION_FIELD_TYPES = {"dropdown", "checkbox", "radio"}

_ONBOARDING_FIELD_EXAMPLE = {
    "label": "Enterprise Name",
    "field_key": "business_legal_name",
    "field_type": "text",
    "placeholder": "Pinnacle Wellness Co.",
    "help_text": "Legal enterprise name",
    "required": False,
    "locked": False,
    "visible": True,
    "order": 1,
    "options": [],
}

_ONBOARDING_FORM_CREATE_EXAMPLE = {
    "name": "Fitness Enterprise Onboarding Form",
    "description": "Onboarding form for fitness and wellness businesses",
    "entity_type": "enterprise",
    "enterprise_type": "Fitness & Wellness",
    "registration_type": "enterprise",
    "status": "draft",
    "sections": [
        {
            "title": "Business Info",
            "order": 1,
            "fields": [
                _ONBOARDING_FIELD_EXAMPLE,
                {
                    "label": "Trading / DBA Name",
                    "field_key": "business_short_name",
                    "field_type": "text",
                    "placeholder": "Pinnacle Wellness",
                    "help_text": "Short business or trading name",
                    "required": False,
                    "locked": False,
                    "visible": True,
                    "order": 2,
                    "options": [],
                },
            ],
        }
    ],
}

_ONBOARDING_FORM_RESPONSE_EXAMPLE = {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "Fitness Enterprise Onboarding Form",
    "description": "Onboarding form for fitness and wellness businesses",
    "entity_type": "enterprise",
    "enterprise_type": "Fitness & Wellness",
    "registration_type": "enterprise",
    "status": "draft",
    "sections": [
        {
            "id": "550e8400-e29b-41d4-a716-446655440001",
            "title": "Business Info",
            "order": 1,
            "fields": [
                {
                    "id": "550e8400-e29b-41d4-a716-446655440002",
                    **_ONBOARDING_FIELD_EXAMPLE,
                },
                {
                    "id": "550e8400-e29b-41d4-a716-446655440003",
                    "label": "Trading / DBA Name",
                    "field_key": "business_short_name",
                    "field_type": "text",
                    "placeholder": "Pinnacle Wellness",
                    "help_text": "Short business or trading name",
                    "required": False,
                    "locked": False,
                    "visible": True,
                    "order": 2,
                    "options": [],
                },
            ],
        }
    ],
    "created_at": "2026-07-01T12:00:00Z",
    "updated_at": "2026-07-01T12:00:00Z",
    "published_at": None,
}


class OnboardingFieldInput(BaseModel):
    id: UUID | None = Field(None, description="Optional field ID for updates.")
    label: str = Field(..., min_length=1, max_length=255, description="Display label shown in the form.")
    field_key: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Unique field identifier within the form (e.g. business_legal_name).",
    )
    field_type: OnboardingFieldType = Field(..., description="Input control type.")
    placeholder: str | None = Field(None, description="Placeholder text for the input.")
    help_text: str | None = Field(None, description="Helper text shown below the field.")
    required: bool = Field(
        False,
        description="Whether the field is required. Fully configurable from the Form Builder.",
    )
    locked: bool = Field(
        False,
        description="Whether the field is locked in the Form Builder UI. Fully configurable.",
    )
    visible: bool = Field(True, description="Whether the field is visible on the published form.")
    order: int = Field(..., ge=1, description="Display order within the section (1-based).")
    options: list[str] = Field(
        default_factory=list,
        description="Options for dropdown, checkbox, or radio field types.",
    )

    @field_validator("options", mode="before")
    @classmethod
    def normalize_options(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("options must be a list")
        normalized: list[str] = []
        for item in value:
            if isinstance(item, str):
                normalized.append(item)
            elif isinstance(item, dict):
                label = item.get("label") or item.get("value")
                if label is not None:
                    normalized.append(str(label))
        return normalized


class OnboardingSectionInput(BaseModel):
    id: UUID | None = Field(None, description="Optional section ID for updates.")
    title: str = Field(..., min_length=1, max_length=255, description="Section heading.")
    order: int = Field(..., ge=1, description="Display order within the form (1-based).")
    fields: list[OnboardingFieldInput] = Field(default_factory=list, description="Fields in this section.")


class OnboardingFormCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": _ONBOARDING_FORM_CREATE_EXAMPLE},
    )

    name: str = Field(..., min_length=1, max_length=255, description="Form template name.")
    description: str | None = Field(None, description="Optional form description.")
    entity_type: OnboardingFormEntityType = Field(
        "enterprise",
        description="System-level entity type. Currently only 'enterprise' is supported.",
    )
    enterprise_type: str | None = Field(
        default=None,
        max_length=100,
        description=(
            "Industry or vertical category for this form template. "
            "Examples: Healthcare, Fitness & Wellness, Nutrition, Mental Health, Education, Retail."
        ),
        examples=["Fitness & Wellness"],
    )
    registration_type: OnboardingFormRegistrationType | None = Field(
        default=None,
        description=(
            "Builder metadata for registration flow. "
            "'enterprise' = Enterprise/Business registration, "
            "'individual' = Individual/Professional registration."
        ),
        examples=["enterprise"],
    )
    status: OnboardingFormStatus = Field("draft", description="Form lifecycle status.")
    sections: list[OnboardingSectionInput] = Field(
        default_factory=list,
        description="Ordered sections and fields that make up the form structure.",
    )


class OnboardingFormUpdate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": _ONBOARDING_FORM_CREATE_EXAMPLE},
    )

    name: str = Field(..., min_length=1, max_length=255, description="Form template name.")
    description: str | None = Field(None, description="Optional form description.")
    entity_type: OnboardingFormEntityType = Field(
        "enterprise",
        description="System-level entity type. Currently only 'enterprise' is supported.",
    )
    enterprise_type: str | None = Field(
        default=None,
        max_length=100,
        description=(
            "Industry or vertical category for this form template. "
            "Examples: Healthcare, Fitness & Wellness, Nutrition, Mental Health, Education, Retail."
        ),
        examples=["Fitness & Wellness"],
    )
    registration_type: OnboardingFormRegistrationType | None = Field(
        default=None,
        description=(
            "Builder metadata for registration flow. "
            "'enterprise' = Enterprise/Business registration, "
            "'individual' = Individual/Professional registration."
        ),
        examples=["enterprise"],
    )
    status: OnboardingFormStatus = Field("draft", description="Form lifecycle status.")
    sections: list[OnboardingSectionInput] = Field(
        default_factory=list,
        description="Ordered sections and fields that make up the form structure.",
    )


class OnboardingFormPublishRequest(BaseModel):
    status: OnboardingFormStatus = "published"


class OnboardingFormUnpublishRequest(BaseModel):
    status: OnboardingFormStatus = "draft"


class OnboardingFormDuplicateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class OnboardingFieldResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    label: str
    field_key: str
    field_type: OnboardingFieldType
    placeholder: str | None = None
    help_text: str | None = None
    required: bool
    locked: bool
    visible: bool
    order: int
    options: list[str] = Field(default_factory=list)


class OnboardingSectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    order: int
    fields: list[OnboardingFieldResponse] = Field(default_factory=list)


class OnboardingFormResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"example": _ONBOARDING_FORM_RESPONSE_EXAMPLE},
    )

    id: UUID
    name: str
    description: str | None = None
    entity_type: OnboardingFormEntityType = Field(
        description="System-level entity type. Currently only 'enterprise' is supported.",
    )
    enterprise_type: str | None = Field(
        None,
        description="Industry or vertical category for this form template.",
    )
    registration_type: OnboardingFormRegistrationType | None = Field(
        None,
        description="Builder metadata: 'enterprise' or 'individual' registration flow.",
    )
    status: OnboardingFormStatus
    sections: list[OnboardingSectionResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None = None


class OnboardingFormListItemResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "name": "Fitness Enterprise Onboarding Form",
                "description": "Onboarding form for fitness and wellness businesses",
                "entity_type": "enterprise",
                "enterprise_type": "Fitness & Wellness",
                "registration_type": "enterprise",
                "status": "draft",
                "sections_count": 1,
                "fields_count": 2,
                "assigned_count": 0,
                "created_at": "2026-07-01T12:00:00Z",
                "updated_at": "2026-07-01T12:00:00Z",
            }
        },
    )

    id: UUID
    name: str
    description: str | None = None
    entity_type: OnboardingFormEntityType
    enterprise_type: str | None = Field(
        None,
        description="Industry or vertical category for this form template.",
    )
    registration_type: OnboardingFormRegistrationType | None = Field(
        None,
        description="Builder metadata: 'enterprise' or 'individual' registration flow.",
    )
    status: OnboardingFormStatus
    sections_count: int = 0
    fields_count: int = 0
    assigned_count: int = 0
    created_at: datetime
    updated_at: datetime


class OnboardingFormPaginatedResponse(PaginatedResponse[OnboardingFormListItemResponse]):
    pass


class OnboardingFormUpdateResponse(BaseModel):
    id: UUID
    name: str
    status: OnboardingFormStatus
    sections_count: int
    fields_count: int
    updated_at: datetime


class OnboardingFormPublishResponse(BaseModel):
    id: UUID
    status: OnboardingFormStatus
    published_at: datetime


class OnboardingFormUnpublishResponse(BaseModel):
    id: UUID
    status: OnboardingFormStatus
    updated_at: datetime


class OnboardingFormDuplicateResponse(BaseModel):
    id: UUID
    name: str
    status: OnboardingFormStatus
    sections_count: int
    fields_count: int
    created_at: datetime


class OnboardingFormDeleteResponse(BaseModel):
    id: UUID
    status: OnboardingFormStatus
    message: str
