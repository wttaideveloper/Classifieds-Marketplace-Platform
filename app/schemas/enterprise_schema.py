from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.schemas.common_schema import EnterpriseStatusLabel


_ENTERPRISE_CREATE_EXAMPLE = {
    "business_short_name": "Spin Health",
    "business_legal_name": "Spin Health Co Pvt Ltd",
    "business_description": "We provide Top-Class fitness programs for kids",
    "business_email": "contact@spinhealth.com",
    "business_phone": "+1-555-0100",
    "registration_number": "REG-2024-001",
    "business_category": "Fitness",
    "website_url": "https://spinhealth.com",
    "year_founded": 2018,
    "primary_contact_name": "Jane Doe",
    "primary_contact_title": "Owner",
    "secondary_email": "support@spinhealth.com",
    "secondary_phone": "+1-555-0101",
    "suite_unit": "Suite 200",
    "brand_color": "#1A73E8",
    "tagline": "Move better, live stronger",
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
    )

    business_email: EmailStr = Field(
        ...,
        description="Primary contact email for the business.",
    )

    business_phone: str | None = None

    registered_address: str | None = None

    business_address: str | None = None

    communication_address: str | None = None

    suite_unit: str | None = Field(
        None,
        description="Suite or unit number for the business address.",
    )

    logo_url: str | None = None

    business_images: str | None = None

    registration_number: str | None = Field(
        None,
        description="Government or business registration number.",
    )

    business_category: str | None = Field(
        None,
        description="Business category or industry.",
        examples=["Fitness"],
    )

    website_url: str | None = Field(
        None,
        description="Public website URL.",
    )

    year_founded: int | None = Field(
        None,
        description="Year the business was founded.",
        examples=[2018],
    )

    primary_contact_name: str | None = None

    primary_contact_title: str | None = None

    secondary_email: EmailStr | None = None

    secondary_phone: str | None = None

    brand_color: str | None = Field(
        None,
        description="Brand color hex code.",
        examples=["#1A73E8"],
    )

    tagline: str | None = None

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
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "business_category": "Fitness",
                "website_url": "https://spinhealth.com",
                "registration_number": "REG-2024-001",
                "year_founded": 2018,
                "primary_contact_name": "Jane Doe",
                "primary_contact_title": "Owner",
                "secondary_email": "support@spinhealth.com",
                "secondary_phone": "+1-555-0101",
                "suite_unit": "Suite 200",
                "brand_color": "#1A73E8",
                "tagline": "Move better, live stronger",
                "logo_url": "https://cdn.example.com/logo.png",
                "business_images": "https://cdn.example.com/banner.png",
            }
        }
    )

    business_short_name: str | None = None
    business_legal_name: str | None = None
    business_description: str | None = None
    business_email: EmailStr | None = None
    business_phone: str | None = None
    registered_address: str | None = None
    business_address: str | None = None
    communication_address: str | None = None
    suite_unit: str | None = None
    logo_url: str | None = None
    business_images: str | None = None
    registration_number: str | None = None
    business_category: str | None = None
    website_url: str | None = None
    year_founded: int | None = None
    primary_contact_name: str | None = None
    primary_contact_title: str | None = None
    secondary_email: EmailStr | None = None
    secondary_phone: str | None = None
    brand_color: str | None = None
    tagline: str | None = None
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
    suite_unit: str | None
    logo_url: str | None
    business_images: str | None
    registration_number: str | None
    business_category: str | None
    website_url: str | None
    year_founded: int | None
    primary_contact_name: str | None
    primary_contact_title: str | None
    secondary_email: str | None
    secondary_phone: str | None
    brand_color: str | None
    tagline: str | None
    status: bool
    created_at: datetime | None = None


class EnterpriseListItemResponse(EnterpriseResponse):
    category: str | None = Field(
        None,
        description="Business category (alias of business_category).",
    )
    status_label: EnterpriseStatusLabel = Field(
        ...,
        description="Display status: active, inactive, or pending.",
    )
    members_count: int = Field(
        0,
        description="Computed member count (not yet tracked in database).",
    )
    revenue: float = Field(
        0,
        description="Computed revenue (not yet tracked in database).",
    )
    joined_date: date | None = Field(
        None,
        description="Date the enterprise joined, derived from created_at.",
    )


class EnterpriseDetailResponse(EnterpriseResponse):
    category: str | None = Field(
        None,
        description="Business category (alias of business_category).",
    )
    status_label: EnterpriseStatusLabel = Field(
        ...,
        description="Display status: active, inactive, or pending.",
    )
    members_count: int = Field(
        0,
        description="Computed member count (not yet tracked in database).",
    )
    revenue: float = Field(
        0,
        description="Computed revenue (not yet tracked in database).",
    )
    rating: float = Field(
        0,
        description="Computed rating (not yet tracked in database).",
    )
