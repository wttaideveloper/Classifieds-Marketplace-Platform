from uuid import UUID

from pydantic import BaseModel
from pydantic import Field


class ProductCreate(BaseModel):
    enterprise_id: UUID = Field(
        ...,
        description="Enterprise ID",
        examples=["550e8400-e29b-41d4-a716-446655440000"]
    )

    product_name: str = Field(
        ...,
        description="Product Name",
        examples=["Dell Latitude 5440"]
    )

    product_description: str | None = Field(
        None,
        description="Product Description",
        examples=["Business laptop with Intel Core i7 processor"]
    )

    product_category: str = Field(
        ...,
        description="Product Category",
        examples=["Laptop"]
    )

    product_price: float = Field(
        ...,
        description="Product Price",
        examples=[65000.00]
    )

    product_images: str | None = Field(
        None,
        description="Product Image URL",
        examples=["https://example.com/images/laptop.png"]
    )

    product_status: bool = Field(
        default=True,
        description="Product Status"
    )


class ProductUpdate(BaseModel):
    product_name: str | None = Field(
        None,
        description="Updated Product Name",
        examples=["Dell Latitude 5450"]
    )

    product_description: str | None = Field(
        None,
        description="Updated Product Description",
        examples=["Updated business laptop"]
    )

    product_category: str | None = Field(
        None,
        description="Updated Product Category",
        examples=["Laptop"]
    )

    product_price: float | None = Field(
        None,
        description="Updated Product Price",
        examples=[70000.00]
    )

    product_images: str | None = Field(
        None,
        description="Updated Product Image URL",
        examples=["https://example.com/images/laptop-new.png"]
    )

    product_status: bool | None = Field(
        None,
        description="Product Status"
    )


class ProductResponse(BaseModel):
    id: UUID

    enterprise_id: UUID

    product_name: str

    product_description: str | None

    product_category: str

    product_price: float

    product_images: str | None

    product_status: bool

    class Config:
        from_attributes = True