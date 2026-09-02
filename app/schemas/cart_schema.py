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


class ShippingAddress(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=120, description="Recipient full name")
    phone: str = Field(..., min_length=7, max_length=20, description="Contact phone")
    line1: str = Field(..., min_length=1, max_length=255, description="Address line 1")
    line2: str | None = Field(default=None, max_length=255, description="Address line 2")
    city: str = Field(..., min_length=1, max_length=100)
    state: str = Field(..., min_length=1, max_length=100)
    zip: str = Field(..., min_length=1, max_length=20, description="ZIP / postal code")
    country: str = Field(..., min_length=1, max_length=100)


class CheckoutRequest(BaseModel):
    shipping_address: ShippingAddress = Field(..., description="Shipping address object")


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
    shipping_address: ShippingAddress | None = None
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


# ---- Order Status & Refund Schemas ----

class OrderStatusUpdate(BaseModel):
    status: str = Field(..., description="New status: confirmed|shipped|delivered|cancelled|completed")
    reason: str | None = Field(None, description="Reason for status change")

class OrderRefundRequest(BaseModel):
    reason: str | None = Field(None, description="Reason for refund")
    amount: str | None = Field(None, description="Partial amount if partial refund")

class OrderRefundResponse(BaseModel):
    id: UUID
    order_id: UUID
    status: str
    payment_status: str | None = None
    refund_reason: str | None = None
    message: str
