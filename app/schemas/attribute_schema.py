from uuid import UUID

from pydantic import BaseModel
from pydantic import Field


class AttributeCreate(BaseModel):
    entity_type: str = Field(
        ...,
        description="Entity type (enterprise, product, service)",
        examples=["enterprise"]
    )

    entity_id: UUID = Field(
        ...,
        description="Entity UUID"
    )

    attribute_name: str = Field(
        ...,
        description="Attribute name",
        examples=["License Number"]
    )

    attribute_value: str = Field(
        ...,
        description="Attribute value",
        examples=["LIC-12345"]
    )

    attribute_type: str = Field(
        ...,
        description="Attribute type",
        examples=["text"]
    )


class AttributeUpdate(BaseModel):
    attribute_name: str | None = Field(
        None,
        description="Updated attribute name",
        examples=["GST Number"]
    )

    attribute_value: str | None = Field(
        None,
        description="Updated attribute value",
        examples=["GST123456789"]
    )

    attribute_type: str | None = Field(
        None,
        description="Updated attribute type",
        examples=["text"]
    )


class AttributeResponse(BaseModel):
    id: UUID

    entity_type: str

    entity_id: UUID

    attribute_name: str

    attribute_value: str

    attribute_type: str

    class Config:
        from_attributes = True