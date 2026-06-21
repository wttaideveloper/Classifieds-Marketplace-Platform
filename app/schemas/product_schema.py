from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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
    model_config = ConfigDict(from_attributes=True)

    id: UUID

    enterprise_id: UUID

    product_name: str

    product_description: str | None

    product_category: str

    product_price: float

    product_images: str | None

    product_status: bool


class ProductListItemResponse(ProductResponse):
    rating: float = Field(
        0,
        description="Average product rating (placeholder until stored in database).",
    )


class ProductDetailResponse(ProductResponse):
    enterprise_name: str | None = Field(
        None,
        description="Short name of the owning enterprise.",
    )
    rating: float = Field(
        0,
        description="Average product rating (placeholder until stored in database).",
    )
    length: float | None = Field(
        None,
        description="Product length (placeholder until stored in database).",
    )
    width: float | None = Field(
        None,
        description="Product width (placeholder until stored in database).",
    )
    thick: float | None = Field(
        None,
        description="Product thickness (placeholder until stored in database).",
    )
    stock_count: int = Field(
        0,
        description="Available stock count (placeholder until stored in database).",
    )
