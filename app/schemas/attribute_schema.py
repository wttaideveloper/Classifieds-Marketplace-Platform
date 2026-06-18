from uuid import UUID
from pydantic import BaseModel, Field


class AttributeCreate(BaseModel):
    entity_type: str = Field(
        ...,
        description="Entity type",
        example="product"
    )

    entity_id: UUID = Field(
        ...,
        description="Entity UUID",
        example="550e8400-e29b-41d4-a716-446655440000"
    )

    key: str = Field(
        ...,
        description="Attribute name",
        example="Color"
    )

    value: str = Field(
        ...,
        description="Attribute value",
        example="Blue"
    )


class AttributeUpdate(BaseModel):
    key: str | None = Field(
        None,
        description="Updated attribute name",
        example="Size"
    )

    value: str | None = Field(
        None,
        description="Updated attribute value",
        example="Large"
    )


class AttributeResponse(BaseModel):
    id: UUID
    entity_type: str
    entity_id: UUID
    key: str
    value: str

    class Config:
        from_attributes = True