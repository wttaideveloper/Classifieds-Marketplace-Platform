from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CartAddRequest(BaseModel):
    product_id: UUID = Field(..., description="Product to add")
    quantity: int = Field(1, ge=1, le=99, description="Quantity 1-99")


class CartUpdateRequest(BaseModel):
    quantity: int = Field(..., ge=1, le=99, description="New quantity 1-99")


class CartItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    product_id: UUID
    product_name: str | None = None
    product_images: str | None = None
    sku: str | None = None
    quantity: int
    unit_price: float
    currency: str | None = "USD"
    line_total: float | None = None
    stock_quantity: int | None = None


class CartResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    tenant_id: UUID | None = None
    status: str
    items: list[CartItemResponse] = Field(default_factory=list)
    subtotal: float = 0
    currency: str | None = "USD"
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CheckoutRequest(BaseModel):
    shipping_address: str | None = Field(None, description="Optional shipping address")


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    product_id: UUID
    product_name: str
    sku: str | None = None
    quantity: int
    unit_price: float
    line_total: float
    currency: str | None = "USD"


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    tenant_id: UUID | None = None
    status: str
    total: float
    currency: str | None = "USD"
    items: list[OrderItemResponse] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OrderListItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    tenant_id: UUID | None = None
    status: str
    total: float
    currency: str | None = "USD"
    item_count: int = 0
    created_at: datetime | None = None
