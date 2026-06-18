from pydantic import BaseModel, Field
from uuid import UUID


class ProductCreate(BaseModel):
    name: str = Field(
        ...,
        description="Product name",
        example="Dell Latitude 5440"
    )

    description: str | None = Field(
        None,
        description="Product description",
        example="Business laptop with Intel Core i7 processor"
    )

    enterprise_id: UUID = Field(
        ...,
        description="Associated enterprise ID"
    )


class ProductUpdate(BaseModel):
    name: str | None = Field(
        None,
        description="Updated product name",
        example="Dell Latitude 5450"
    )

    description: str | None = Field(
        None,
        description="Updated product description"
    )


class ProductResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    enterprise_id: UUID

    class Config:
        from_attributes = True