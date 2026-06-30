from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.schemas.common_schema import EntityStatus, EnterpriseStatusLabel, PaginatedResponse


_ENTERPRISE_CREATE_EXAMPLE = {
    "business_short_name": "Spin Health",
    "business_legal_name": "Spin Health Co Pvt Ltd",
    "business_description": "We provide Top-Class fitness programs for kids",
    "business_email": "contact@spinhealth.com",
    "business_phone": "+1-555-0100",
    "website": "https://spinhealth.com",
    "banner_url": "https://cdn.example.com/banner.png",
    "status": "draft",
}


class EnterpriseCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": _ENTERPRISE_CREATE_EXAMPLE},
    )

    tenant_id: UUID | None = Field(
        None,
        description="Tenant identifier for multi-tenant support.",
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
    website: str | None = Field(None, description="Public website URL.")
    logo_url: str | None = None
    banner_url: str | None = None
    status: EntityStatus = Field("draft", description="Enterprise lifecycle status.")
    suite_unit: str | None = Field(
        None,
        description="Suite or unit number for the business address.",
    )
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
        description="Deprecated alias for website.",
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

        if normalized.get("website_url") and not normalized.get("website"):
            normalized["website"] = normalized["website_url"]

        if normalized.get("business_images") and not normalized.get("banner_url"):
            normalized["banner_url"] = normalized["business_images"]

        if "status" in normalized and isinstance(normalized["status"], bool):
            normalized["status"] = "active" if normalized["status"] else "inactive"

        return normalized

    @model_validator(mode="after")
    def apply_defaults(self):
        if not self.business_legal_name:
            self.business_legal_name = self.business_short_name
        if not self.website and self.website_url:
            self.website = self.website_url
        if not self.banner_url and self.business_images:
            self.banner_url = self.business_images
        return self


class EnterpriseUpdate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "business_category": "Fitness",
                "website": "https://spinhealth.com",
                "status": "active",
            }
        }
    )

    tenant_id: UUID | None = None
    business_short_name: str | None = None
    business_legal_name: str | None = None
    business_description: str | None = None
    business_email: EmailStr | None = None
    business_phone: str | None = None
    registered_address: str | None = None
    business_address: str | None = None
    communication_address: str | None = None
    website: str | None = None
    logo_url: str | None = None
    banner_url: str | None = None
    status: EntityStatus | None = None
    suite_unit: str | None = None
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

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_aliases(cls, data):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if normalized.get("website_url") and not normalized.get("website"):
            normalized["website"] = normalized["website_url"]
        if normalized.get("business_images") and not normalized.get("banner_url"):
            normalized["banner_url"] = normalized["business_images"]
        if "status" in normalized and isinstance(normalized["status"], bool):
            normalized["status"] = "active" if normalized["status"] else "inactive"
        return normalized


class EnterpriseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None = None
    business_short_name: str
    business_legal_name: str
    business_description: str | None = None
    business_email: str
    business_phone: str | None = None
    registered_address: str | None = None
    business_address: str | None = None
    communication_address: str | None = None
    website: str | None = None
    logo_url: str | None = None
    banner_url: str | None = None
    status: EntityStatus
    suite_unit: str | None = None
    business_images: str | None = None
    registration_number: str | None = None
    business_category: str | None = None
    website_url: str | None = None
    year_founded: int | None = None
    primary_contact_name: str | None = None
    primary_contact_title: str | None = None
    secondary_email: str | None = None
    secondary_phone: str | None = None
    brand_color: str | None = None
    tagline: str | None = None
    created_at: datetime | None = None


class EnterpriseListItemResponse(EnterpriseResponse):
    category: str | None = Field(
        None,
        description="Business category (alias of business_category).",
    )
    status_label: EnterpriseStatusLabel = Field(
        ...,
        description="Display status: active, inactive, draft, or pending.",
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
        description="Display status: active, inactive, draft, or pending.",
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


class EnterprisePaginatedResponse(PaginatedResponse[EnterpriseListItemResponse]):
    pass
