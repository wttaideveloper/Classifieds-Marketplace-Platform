from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common_schema import PaginatedResponse

OnboardingFormStatus = Literal["draft", "published", "inactive"]
OnboardingFormEntityType = Literal["enterprise"]
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


class OnboardingFieldInput(BaseModel):
    id: UUID | None = None
    label: str = Field(..., min_length=1, max_length=255)
    field_key: str = Field(..., min_length=1, max_length=100)
    field_type: OnboardingFieldType
    placeholder: str | None = None
    help_text: str | None = None
    required: bool = False
    locked: bool = False
    visible: bool = True
    order: int = Field(..., ge=1)
    options: list[str] = Field(default_factory=list)

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
    id: UUID | None = None
    title: str = Field(..., min_length=1, max_length=255)
    order: int = Field(..., ge=1)
    fields: list[OnboardingFieldInput] = Field(default_factory=list)


class OnboardingFormCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    entity_type: OnboardingFormEntityType = "enterprise"
    status: OnboardingFormStatus = "draft"
    sections: list[OnboardingSectionInput] = Field(default_factory=list)


class OnboardingFormUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    entity_type: OnboardingFormEntityType = "enterprise"
    status: OnboardingFormStatus = "draft"
    sections: list[OnboardingSectionInput] = Field(default_factory=list)


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
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None = None
    entity_type: OnboardingFormEntityType
    status: OnboardingFormStatus
    sections: list[OnboardingSectionResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None = None


class OnboardingFormListItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None = None
    entity_type: OnboardingFormEntityType
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
