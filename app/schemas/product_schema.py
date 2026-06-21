from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProductCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "enterprise_id": "550e8400-e29b-41d4-a716-446655440000",
                "product_name": "Yoga Mat Pro",
                "product_description": "Non-slip premium yoga mat",
                "product_category": "Fitness",
                "product_price": 49.99,
                "product_images": "https://example.com/images/yoga-mat.png",
                "product_status": True,
                "sku": "YM-PRO-001",
                "barcode_upc": "012345678905",
                "weight": 1.2,
                "dimensions": "72x24x0.2 in",
                "length": 72.0,
                "width": 24.0,
                "thick": 0.2,
                "sale_price": 39.99,
                "cost_price": 20.0,
                "tax_class": "standard",
                "currency": "USD",
                "stock_quantity": 100,
                "low_stock_alert_threshold": 10,
                "stock_management": "enabled",
                "publish_status": "published",
            }
        }
    )

    enterprise_id: UUID = Field(..., description="Enterprise ID")

    product_name: str = Field(..., description="Product name")

    product_description: str | None = None

    product_category: str = Field(..., description="Product category")

    product_price: float = Field(..., description="Base product price")

    product_images: str | None = None

    product_status: bool = Field(default=True, description="Active/inactive flag")

    sku: str | None = Field(None, description="Stock keeping unit")

    barcode_upc: str | None = Field(None, description="Barcode or UPC")

    weight: float | None = Field(None, description="Product weight")

    dimensions: str | None = Field(
        None,
        description="Product dimensions (free-form, e.g. 72x24x0.2 in)",
    )

    length: float | None = Field(None, description="Product length")

    width: float | None = Field(None, description="Product width")

    thick: float | None = Field(None, description="Product thickness")

    sale_price: float | None = Field(None, description="Promotional sale price")

    cost_price: float | None = Field(None, description="Cost price")

    tax_class: str | None = Field(None, description="Tax classification")

    currency: str | None = Field("USD", description="ISO currency code")

    stock_quantity: int | None = Field(0, description="Available stock quantity")

    low_stock_alert_threshold: int | None = Field(
        None,
        description="Threshold for low-stock alerts",
    )

    stock_management: str | None = Field(
        None,
        description="Stock management mode (e.g. enabled, disabled, manual)",
    )

    publish_status: str | None = Field(
        "draft",
        description="Publication status (e.g. draft, published)",
    )


class ProductUpdate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "sku": "YM-PRO-001",
                "barcode_upc": "012345678905",
                "weight": 1.2,
                "dimensions": "72x24x0.2 in",
                "length": 72.0,
                "width": 24.0,
                "thick": 0.2,
                "sale_price": 39.99,
                "cost_price": 20.0,
                "tax_class": "standard",
                "currency": "USD",
                "stock_quantity": 100,
                "low_stock_alert_threshold": 10,
                "stock_management": "enabled",
                "publish_status": "published",
                "product_images": "https://cdn.example.com/yoga-mat.png",
            }
        }
    )

    product_name: str | None = None
    product_description: str | None = None
    product_category: str | None = None
    product_price: float | None = None
    product_images: str | None = None
    product_status: bool | None = None
    sku: str | None = None
    barcode_upc: str | None = None
    weight: float | None = None
    dimensions: str | None = None
    length: float | None = None
    width: float | None = None
    thick: float | None = None
    sale_price: float | None = None
    cost_price: float | None = None
    tax_class: str | None = None
    currency: str | None = None
    stock_quantity: int | None = None
    low_stock_alert_threshold: int | None = None
    stock_management: str | None = None
    publish_status: str | None = None


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
    sku: str | None
    barcode_upc: str | None
    weight: float | None
    dimensions: str | None
    length: float | None
    width: float | None
    thick: float | None
    sale_price: float | None
    cost_price: float | None
    tax_class: str | None
    currency: str | None
    stock_quantity: int | None
    low_stock_alert_threshold: int | None
    stock_management: str | None
    publish_status: str | None
    created_at: datetime | None = None


class ProductListItemResponse(ProductResponse):
    rating: float = Field(
        0,
        description="Computed rating (not yet tracked in database).",
    )


class ProductDetailResponse(ProductResponse):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440002",
                "enterprise_id": "550e8400-e29b-41d4-a716-446655440000",
                "enterprise_name": "Spin Health",
                "product_name": "Yoga Mat Pro",
                "length": 72.0,
                "width": 24.0,
                "thick": 0.2,
                "stock_count": 100,
            }
        }
    )

    enterprise_name: str | None = Field(
        None,
        description="Short name of the owning enterprise.",
    )
    rating: float = Field(
        0,
        description="Computed rating (not yet tracked in database).",
    )
    stock_count: int | None = Field(
        None,
        description="Available stock (alias of stock_quantity).",
    )
