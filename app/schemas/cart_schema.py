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


class ShippingAddressOut(BaseModel):
    """The address as stored, for answers. Every part is optional: an order placed before the address was compulsory (or by another path) can have an
    incomplete one, and showing it must not turn the order into a 500. Checkout still requires the full ShippingAddress."""

    full_name: str | None = None
    phone: str | None = None
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    country: str | None = None


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
    shipping_address: ShippingAddressOut | None = None
    items: list[OrderItemResponse] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OrderCancelRequest(BaseModel):
    reason: str | None = Field(None, max_length=500, description="Required for a paid order")


class OrderDetailResponse(OrderResponse):
    cancel_mode: str | None
    refund_due: bool
    can_cancel: bool
    cancel_blocked_reason: str | None
    can_refund: bool
    refund_blocked_reason: str | None
    payment_id: UUID | None
    refund_id: UUID | None
    refund_status: str | None
    refund_amount: str | None = Field(description="Latest refund amount in payment currency")
    refund_currency: str | None = None
    refund_window_days: int | None = None
    payment_status: str | None = None
    # fulfilment: set when the store ships / delivers (the customer's tracking screen reads these)
    carrier: str | None = None
    tracking_number: str | None = None
    tracking_url: str | None = None
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    cancellation: dict | None = Field(None, description="requested_at, reason, decision_reason, cancelled_at, cancelled_by_role")
    history: list[dict] = Field(default_factory=list, description="Every status change: action, from_status, to_status, actor_role, reason, at")
    # staff only (the Enterprise Admin / Super Admin viewing a customer's order)
    allowed_statuses: list[str] | None = Field(None, description="The statuses the order can be moved to now. Staff only.")
    customer: dict | None = Field(None, description="user_id, name and phone from the shipping address. Staff only.")


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
    carrier: str | None = None
    tracking_number: str | None = None
    tracking_url: str | None = None
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
