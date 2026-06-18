from uuid import UUID
from pydantic import BaseModel, Field


class EnterpriseCreate(BaseModel):
    name: str = Field(
        ...,
        description="Enterprise name",
        example="ABC Pvt Ltd"
    )

    description: str | None = Field(
        None,
        description="Enterprise description",
        example="Leading software solutions provider"
    )


class EnterpriseUpdate(BaseModel):
    name: str | None = Field(
        None,
        description="Updated enterprise name",
        example="ABC Technologies Pvt Ltd"
    )

    description: str | None = Field(
        None,
        description="Updated enterprise description",
        example="Global technology services company"
    )


class EnterpriseResponse(BaseModel):
    id: UUID
    name: str
    description: str | None

    class Config:
        from_attributes = True