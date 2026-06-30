from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common_schema import EntityStatus, PaginatedResponse


class ProductCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "tenant_id": "550e8400-e29b-41d4-a716-446655440000",
                "enterprise_id": "550e8400-e29b-41d4-a716-446655440001",
                "location_id": "550e8400-e29b-41d4-a716-446655440002",
                "product_name": "Yoga Mat Pro",
                "description": "Non-slip premium yoga mat",
                "category": "Fitness",
                "price": 49.99,
                "currency": "USD",
                "image_urls": "https://example.com/images/yoga-mat.png",
                "status": "draft",
            }
        }
    )

    tenant_id: UUID | None = Field(None, description="Tenant identifier.")
    enterprise_id: UUID = Field(..., description="Enterprise ID")
    location_id: UUID | None = Field(None, description="Enterprise location ID")
    product_name: str = Field(..., description="Product name")
    description: str | None = Field(None, description="Product description")
    category: str = Field(..., description="Product category")
    price: float = Field(..., description="Base product price")
    currency: str | None = Field("USD", description="ISO currency code")
    image_urls: str | None = Field(None, description="Product image URLs")
    status: EntityStatus = Field("draft", description="Product lifecycle status.")
    product_description: str | None = None
    product_category: str | None = None
    product_price: float | None = None
    product_images: str | None = None
    product_status: bool | None = None
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

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_aliases(cls, data):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if normalized.get("product_description") and not normalized.get("description"):
            normalized["description"] = normalized["product_description"]
        if normalized.get("product_category") and not normalized.get("category"):
            normalized["category"] = normalized["product_category"]
        if normalized.get("product_price") is not None and normalized.get("price") is None:
            normalized["price"] = normalized["product_price"]
        if normalized.get("product_images") and not normalized.get("image_urls"):
            normalized["image_urls"] = normalized["product_images"]
        if "product_status" in normalized and "status" not in normalized:
            normalized["status"] = (
                "active" if normalized["product_status"] else "inactive"
            )
        return normalized

    def to_model_data(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "enterprise_id": self.enterprise_id,
            "location_id": self.location_id,
            "product_name": self.product_name,
            "product_description": self.description or self.product_description,
            "product_category": self.category or self.product_category,
            "product_price": self.price if self.price is not None else self.product_price,
            "product_images": self.image_urls or self.product_images,
            "currency": self.currency,
            "status": self.status,
            "product_status": self.status == "active",
            "sku": self.sku,
            "barcode_upc": self.barcode_upc,
            "weight": self.weight,
            "dimensions": self.dimensions,
            "length": self.length,
            "width": self.width,
            "thick": self.thick,
            "sale_price": self.sale_price,
            "cost_price": self.cost_price,
            "tax_class": self.tax_class,
            "stock_quantity": self.stock_quantity,
            "low_stock_alert_threshold": self.low_stock_alert_threshold,
            "stock_management": self.stock_management,
            "publish_status": self.publish_status,
        }


class ProductUpdate(BaseModel):
    tenant_id: UUID | None = None
    location_id: UUID | None = None
    product_name: str | None = None
    description: str | None = None
    category: str | None = None
    price: float | None = None
    currency: str | None = None
    image_urls: str | None = None
    status: EntityStatus | None = None
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
    stock_quantity: int | None = None
    low_stock_alert_threshold: int | None = None
    stock_management: str | None = None
    publish_status: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_aliases(cls, data):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if normalized.get("product_description") and not normalized.get("description"):
            normalized["description"] = normalized["product_description"]
        if normalized.get("product_category") and not normalized.get("category"):
            normalized["category"] = normalized["product_category"]
        if normalized.get("product_price") is not None and normalized.get("price") is None:
            normalized["price"] = normalized["product_price"]
        if normalized.get("product_images") and not normalized.get("image_urls"):
            normalized["image_urls"] = normalized["product_images"]
        if "product_status" in normalized and "status" not in normalized:
            normalized["status"] = (
                "active" if normalized["product_status"] else "inactive"
            )
        return normalized

    def to_model_data(self) -> dict:
        data = self.model_dump(exclude_unset=True)
        mapped: dict = {}
        field_map = {
            "description": "product_description",
            "category": "product_category",
            "price": "product_price",
            "image_urls": "product_images",
        }
        for key, value in data.items():
            if key in field_map:
                mapped[field_map[key]] = value
            elif key not in {"product_description", "product_category", "product_price", "product_images"}:
                mapped[key] = value
        if "status" in mapped:
            mapped["product_status"] = mapped["status"] == "active"
        return mapped


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None = None
    enterprise_id: UUID
    location_id: UUID | None = None
    product_name: str
    description: str | None = None
    category: str
    price: float
    currency: str | None
    image_urls: str | None = None
    status: EntityStatus
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
    stock_quantity: int | None = None
    low_stock_alert_threshold: int | None = None
    stock_management: str | None = None
    publish_status: str | None = None
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


class ProductPaginatedResponse(PaginatedResponse[ProductListItemResponse]):
    pass
