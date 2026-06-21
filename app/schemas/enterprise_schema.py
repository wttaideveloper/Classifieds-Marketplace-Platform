from uuid import UUID
from datetime import date

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.schemas.common_schema import AvailabilityResponse, EnterpriseStatusLabel


_ENTERPRISE_CREATE_EXAMPLE = {
    "business_short_name": "Spin Health",
    "business_legal_name": "Spin Health Co Pvt Ltd",
    "business_description": "We provide Top-Class fitness programs for kids",
    "business_email": "contact@spinhealth.com",
}


class EnterpriseCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": _ENTERPRISE_CREATE_EXAMPLE},
    )

    business_short_name: str = Field(
        ...,
        description="Short display name for the business.",
        examples=["Spin Health"],
    )

    business_legal_name: str | None = Field(
        None,
        description=(
            "Registered legal business name. "
            "Defaults to business_short_name when omitted."
        ),
        examples=["Spin Health Co Pvt Ltd"],
    )

    business_description: str | None = Field(
        None,
        description="Optional description of the business.",
        examples=["We provide Top-Class fitness programs for kids"],
    )

    business_email: EmailStr = Field(
        ...,
        description="Primary contact email for the business.",
        examples=["contact@spinhealth.com"],
    )

    business_phone: str | None = Field(
        None,
        description="Optional business phone number.",
    )

    registered_address: str | None = Field(
        None,
        description="Registered business address.",
    )

    business_address: str | None = Field(
        None,
        description="Operating business address.",
    )

    communication_address: str | None = Field(
        None,
        description="Mailing or communication address.",
    )

    logo_url: str | None = Field(
        None,
        description="URL to the business logo.",
    )

    business_images: str | None = Field(
        None,
        description="URL or serialized list of business image URLs.",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_aliases(cls, data):
        if not isinstance(data, dict):
            return data

        normalized = dict(data)

        if "name" in normalized and "business_short_name" not in normalized:
            normalized["business_short_name"] = normalized.pop("name")

        if "description" in normalized and "business_description" not in normalized:
            normalized["business_description"] = normalized.pop("description")

        return normalized

    @model_validator(mode="after")
    def apply_defaults(self):
        if not self.business_legal_name:
            self.business_legal_name = self.business_short_name
        return self


class EnterpriseUpdate(BaseModel):
    business_short_name: str | None = None

    business_legal_name: str | None = None

    business_description: str | None = None

    business_email: EmailStr | None = None

    business_phone: str | None = None

    registered_address: str | None = None

    business_address: str | None = None

    communication_address: str | None = None

    logo_url: str | None = None

    business_images: str | None = None

    status: bool | None = None


class EnterpriseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID

    business_short_name: str

    business_legal_name: str

    business_description: str | None

    business_email: str

    business_phone: str | None

    registered_address: str | None

    business_address: str | None

    communication_address: str | None

    logo_url: str | None

    business_images: str | None

    status: bool


class EnterpriseListItemResponse(EnterpriseResponse):
    category: str | None = Field(
        None,
        description="Enterprise category (placeholder until stored in database).",
    )
    status_label: EnterpriseStatusLabel = Field(
        ...,
        description="Display status: active, inactive, or pending.",
        examples=["active"],
    )
    members_count: int = Field(
        0,
        description="Number of members (placeholder until stored in database).",
    )
    revenue: float = Field(
        0,
        description="Total revenue (placeholder until stored in database).",
    )
    joined_date: date | None = Field(
        None,
        description="Date the enterprise joined (placeholder until stored in database).",
    )


class EnterpriseDetailResponse(EnterpriseResponse):
    category: str | None = Field(
        None,
        description="Enterprise category (placeholder until stored in database).",
    )
    status_label: EnterpriseStatusLabel = Field(
        ...,
        description="Display status: active, inactive, or pending.",
        examples=["active"],
    )
    members_count: int = Field(
        0,
        description="Number of members (placeholder until stored in database).",
    )
    revenue: float = Field(
        0,
        description="Total revenue (placeholder until stored in database).",
    )
    rating: float = Field(
        0,
        description="Average rating (placeholder until stored in database).",
    )
