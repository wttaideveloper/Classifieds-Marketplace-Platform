from uuid import UUID
from typing import Optional

from pydantic import BaseModel
from pydantic import field_validator


class AttributeCreate(BaseModel):
    entity_type: str
    entity_id: UUID
    attribute_name: str
    attribute_value: str
    attribute_type: str

    @field_validator("entity_type")
    @classmethod
    def validate_entity_type(cls, value):
        allowed = [
            "enterprise",
            "product",
            "service"
        ]

        if value.lower() not in allowed:
            raise ValueError(
                "entity_type must be enterprise, product or service"
            )

        return value.lower()


class AttributeUpdate(BaseModel):
    attribute_name: Optional[str] = None
    attribute_value: Optional[str] = None
    attribute_type: Optional[str] = None


class AttributeResponse(BaseModel):
    id: UUID
    entity_type: str
    entity_id: UUID
    attribute_name: str
    attribute_value: str
    attribute_type: str

    class Config:
        from_attributes = True