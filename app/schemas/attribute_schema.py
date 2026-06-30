from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common_schema import AttributeType, EntityStatus, EntityType, PaginatedResponse


class AttributeCreate(BaseModel):
    tenant_id: UUID | None = Field(
        None,
        description="Tenant identifier for multi-tenant support.",
    )
    entity_type: EntityType = Field(
        ...,
        description="Entity type (enterprise, product, service)",
        examples=["enterprise"],
    )
    entity_id: UUID = Field(
        ...,
        description="Entity UUID",
    )
    attribute_name: str = Field(
        ...,
        description="Attribute name",
        examples=["License Number"],
    )
    attribute_value: str = Field(
        ...,
        description="Attribute value",
        examples=["LIC-12345"],
    )
    attribute_type: AttributeType = Field(
        ...,
        description="Attribute type",
        examples=["text"],
    )
    is_required: bool = Field(False, description="Whether the attribute is required.")
    status: EntityStatus = Field("active", description="Attribute lifecycle status.")


class AttributeUpdate(BaseModel):
    attribute_name: str | None = Field(
        None,
        description="Updated attribute name",
        examples=["GST Number"],
    )
    attribute_value: str | None = Field(
        None,
        description="Updated attribute value",
        examples=["GST123456789"],
    )
    attribute_type: AttributeType | None = Field(
        None,
        description="Updated attribute type",
        examples=["text"],
    )
    is_required: bool | None = None
    status: EntityStatus | None = None


class AttributeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None = None
    entity_type: EntityType
    entity_id: UUID
    attribute_name: str
    attribute_value: str
    attribute_type: AttributeType
    is_required: bool
    status: EntityStatus
    created_at: datetime | None = None


class AttributePaginatedResponse(PaginatedResponse[AttributeResponse]):
    pass
