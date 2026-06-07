from datetime import datetime
from decimal import Decimal
from typing import List
from uuid import UUID

from pydantic import BaseModel, Field


class AddToCartSchema(BaseModel):
    listing_id: UUID
    quantity: int = Field(..., ge=1)


class UpdateCartItemSchema(BaseModel):
    quantity: int = Field(..., ge=1)


class CartItemResponse(BaseModel):
    cart_item_id: UUID
    listing_id: UUID
    quantity: int
    total_price: Decimal
    merchant_id: UUID


class CartMerchantGroup(BaseModel):
    merchant_id: UUID
    items: List[CartItemResponse]
    merchant_total: Decimal


class CartSummaryResponse(BaseModel):
    success: bool = True
    customer_id: UUID
    items: List[CartItemResponse]
    merchants: List[CartMerchantGroup]
    total_items: int
    cart_total: Decimal


class CartItemEnvelopeResponse(BaseModel):
    success: bool = True
    message: str
    data: CartItemResponse


class CartMessageResponse(BaseModel):
    success: bool = True
    message: str


class CheckoutPreparationResponse(BaseModel):
    success: bool = True
    message: str
    customer_id: UUID
    merchants: List[CartMerchantGroup]
    total_items: int
    cart_total: Decimal
    validated_at: datetime


class SavedCartResponse(BaseModel):
    success: bool = True
    message: str
    customer_id: UUID
    saved_items: int
