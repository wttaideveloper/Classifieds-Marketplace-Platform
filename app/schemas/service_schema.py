from uuid import UUID
from pydantic import BaseModel, Field


class ServiceCreate(BaseModel):
    name: str = Field(
        ...,
        description="Service name",
        example="Website Development"
    )

    description: str | None = Field(
        None,
        description="Service description",
        example="Custom website development services"
    )

    enterprise_id: UUID = Field(
        ...,
        description="Associated enterprise ID"
    )


class ServiceUpdate(BaseModel):
    name: str | None = Field(
        None,
        description="Updated service name",
        example="Advanced Website Development"
    )

    description: str | None = Field(
        None,
        description="Updated service description"
    )


class ServiceResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    enterprise_id: UUID

    class Config:
        from_attributes = True