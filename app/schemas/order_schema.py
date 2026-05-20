from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class OrderStatus(str, Enum):
    Pending = "Pending"
    Confirmed = "Confirmed"
    Processing = "Processing"
    Completed = "Completed"
    Cancelled = "Cancelled"
    Refunded = "Refunded"


class PaymentStatus(str, Enum):
    Pending = "Pending"
    Paid = "Paid"
    Failed = "Failed"
    Refunded = "Refunded"


class OrderItemCreateSchema(BaseModel):
    listing_id: str
    quantity: int = Field(..., ge=1)


class CreateOrderSchema(BaseModel):
    business_id: str
    items: List[OrderItemCreateSchema] = Field(..., min_length=1)
    payment_method: str = Field(..., min_length=1, max_length=50)
    notes: Optional[str] = None


class UpdateOrderStatusSchema(BaseModel):
    order_status: OrderStatus
    remarks: Optional[str] = None


class OrderItemResponseSchema(BaseModel):
    id: str
    listing_id: str
    listing_name: Optional[str] = None
    listing_type: Optional[str] = None
    quantity: int
    unit_price: float
    total_price: float


class OrderResponseSchema(BaseModel):
    order_id: str
    order_number: str
    customer_id: str
    merchant_id: Optional[str] = None
    business_id: str
    total_amount: float
    tax_amount: float
    shipping_amount: float
    discount_amount: float
    final_amount: float
    payment_status: PaymentStatus
    order_status: OrderStatus
    payment_method: str
    notes: Optional[str] = None
    created_at: datetime
    items: List[OrderItemResponseSchema]

