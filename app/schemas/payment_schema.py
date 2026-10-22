from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.currency_schema import Money
from app.schemas.tax_schema import TaxBreakdownRow

PurchaseType = Literal["cart_order", "event_order", "custom"]


class Billing(BaseModel):
    """Where the customer is. It decides the tax. Never put card details here."""
    model_config = ConfigDict(json_schema_extra={"example": {"name": "Priya Sharma", "email": "priya@example.com", "country": "IN", "state": "Karnataka",
                                                              "city": "Bengaluru", "postal_code": "560001", "address_line1": "12 MG Road", "is_business": False}})

    name: str | None = Field(None, max_length=255)
    email: str | None = Field(None, max_length=255)
    country: str | None = Field(None, description="Two-letter country code. Needed for tax")
    state: str | None = Field(None, max_length=60)
    city: str | None = Field(None, max_length=100)
    postal_code: str | None = Field(None, max_length=20)
    address_line1: str | None = Field(None, max_length=255)
    address_line2: str | None = Field(None, max_length=255)
    tax_id: str | None = Field(None, max_length=60, description="GSTIN / VAT number of a business customer")
    is_business: bool = False

    @field_validator("country")
    @classmethod
    def _country(cls, value):
        if value is None or not value.strip():
            return None
        value = value.strip().upper()
        if len(value) != 2 or not value.isalpha():
            raise ValueError("a country is its two-letter code, for example IN")
        return value


class CustomItem(BaseModel):
    description: str = Field(..., min_length=1, max_length=255)
    amount: Decimal = Field(..., ge=0, description="Price of one unit, before tax unless prices_include_tax is set")
    quantity: int = Field(1, ge=1, le=100000)
    category: str = Field("service", max_length=30, description="The kind of item, for tax: service, product, event, training ...")


class PaymentRequest(BaseModel):
    """What to pay for, and how. The amount is never sent: it is read from the order."""
    model_config = ConfigDict(json_schema_extra={"example": {
        "purchase_type": "cart_order", "purchase_id": "3f2a0b6e-0c1d-4a53-9a8e-6a4f3d6f9b10", "currency": "USD",
        "billing": {"country": "IN", "state": "Karnataka"}}})

    purchase_type: PurchaseType
    purchase_id: str | None = Field(None, max_length=64, description="The order id. Not needed for a custom payment")
    currency: str | None = Field(None, description="The currency to pay in. Defaults to the order's own currency")
    saved_method_id: UUID | None = Field(None, description="Pay with one of my saved payment methods")
    return_url: str | None = Field(None, max_length=500, description="Where the gateway sends the customer back after 3-D Secure")
    billing: Billing | None = None
    # custom payments only (a business asking one customer to pay)
    items: list[CustomItem] | None = Field(None, max_length=100)
    item_currency: str | None = Field(None, description="custom: the currency of the item prices")
    customer_user_id: UUID | None = Field(None, description="custom: who is asked to pay")
    customer_email: str | None = Field(None, max_length=255)
    tenant_id: UUID | None = Field(None, description="custom, Super Admin only: the business that is paid")
    description: str | None = Field(None, max_length=255)
    prices_include_tax: bool = False
    capture_method: Literal["automatic", "manual"] = Field("automatic", description="manual: hold the money first and capture it later (custom payments)")

    @field_validator("currency", "item_currency")
    @classmethod
    def _cur(cls, value):
        return value.strip().upper() if value and value.strip() else None


class VerifyRequest(BaseModel):
    """What the browser / app got back from the gateway, passed on as it is. Razorpay: razorpay_order_id, razorpay_payment_id,
    razorpay_signature. Stripe: payment_intent_id (or nothing). Test gateway: test_token (and authenticated for 3-D Secure)."""
    model_config = ConfigDict(extra="allow", json_schema_extra={"example": {"test_token": "tok_success"}})


class RetryRequest(BaseModel):
    currency: str | None = None
    saved_method_id: UUID | None = None
    return_url: str | None = Field(None, max_length=500)
    billing: Billing | None = None

    @field_validator("currency")
    @classmethod
    def _cur(cls, value):
        return value.strip().upper() if value and value.strip() else None


class ReviewDecision(BaseModel):
    action: Literal["approve", "reject"]
    note: str | None = Field(None, max_length=500)


class PaymentLineOut(BaseModel):
    ref: str
    description: str
    category: str
    quantity: int
    unit_amount: Money
    net_amount: Money
    tax_amount: Money
    gross_amount: Money
    charge_net_amount: Money
    charge_tax_amount: Money
    charge_gross_amount: Money
    taxes: list[dict] = Field(default_factory=list)


class RiskOut(BaseModel):
    score: int | None = None
    decision: str | None = None
    reasons: list[str] = Field(default_factory=list)


class PaymentResponse(BaseModel):
    id: UUID
    status: str
    status_label: str
    purchase_type: str
    purchase_id: str
    attempt_no: int
    retry_of_id: UUID | None = None
    tenant_id: UUID | None = None
    customer_user_id: UUID | None = None
    customer_email: str | None = None
    provider: str
    capture_method: str
    # what the item costs, in the currency it is priced in
    item_currency: str
    subtotal_amount: Money
    tax_amount: Money
    total_amount: Money
    # what the customer pays, in the currency they chose
    charge_currency: str
    charge_subtotal: Money
    charge_tax: Money
    charge_total: Money
    exchange_rate: Money = Field(..., description="1 item currency = this many charge currency, locked when the payment was created")
    base_currency: str
    base_total: Money
    refunded_amount: Money
    refundable_amount: Money
    method_brand: str | None = None
    method_last4: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    next_action: dict | None = None
    invoice_id: UUID | None = None
    lines: list[PaymentLineOut] = Field(default_factory=list)
    tax_breakdown: list[TaxBreakdownRow] = Field(default_factory=list)
    reverse_charge: bool = False
    is_tax_exempt: bool = False
    billing: dict | None = None
    risk: RiskOut | None = Field(None, description="Only for the business and the platform")
    needs_reconciliation: bool = False
    expires_at: datetime | None = None
    authorized_at: datetime | None = None
    captured_at: datetime | None = None
    failed_at: datetime | None = None
    cancelled_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None
    actions: list[str] = Field(default_factory=list, description="What the caller can do with it now: verify, retry, cancel, capture, refund, resolve, download_invoice")


class CheckoutResponse(BaseModel):
    payment: PaymentResponse
    checkout: dict[str, Any] = Field(default_factory=dict, description="What the browser / app SDK of the gateway needs to take the payment")


class PaymentListResponse(BaseModel):
    items: list[PaymentResponse]
    total: int
    page: int
    page_size: int


class QuoteResponse(BaseModel):
    """The price shown to the customer before they confirm: items, tax, the exchange rate and the final amount."""
    purchase_type: str
    purchase_id: str
    description: str
    item_currency: str
    charge_currency: str
    exchange_rate: Money
    rate_updated_at: datetime | None = None
    subtotal_amount: Money
    tax_amount: Money
    total_amount: Money
    charge_subtotal: Money
    charge_tax: Money
    charge_total: Money
    base_currency: str
    base_total: Money
    prices_include_tax: bool = False
    is_tax_exempt: bool = False
    reverse_charge: bool = False
    note: str | None = None
    warnings: list[str] = Field(default_factory=list)
    lines: list[PaymentLineOut]
    tax_breakdown: list[TaxBreakdownRow]
    min_amount: Money | None = None
    max_amount: Money | None = None


class WebhookAck(BaseModel):
    received: int
    processed: int
    duplicates: int
    ignored: int


class SavedMethodCreate(BaseModel):
    model_config = ConfigDict(extra="allow", json_schema_extra={"example": {"token": "tok_saved_visa_4242", "exp_month": 12, "exp_year": 2030, "set_default": True}})

    token: str = Field(..., min_length=3, max_length=160, description="The token the gateway's SDK returned. Never a card number")
    set_default: bool = False
    holder_name: str | None = Field(None, max_length=120)

    @field_validator("token")
    @classmethod
    def _no_card_number(cls, value):
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) >= 13 and digits == value.replace(" ", "").replace("-", ""):
            raise ValueError("Never send a card number. Send the token the payment gateway's SDK returned")
        return value


class SavedMethodResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: str
    brand: str | None = None
    last4: str | None = None
    exp_month: int | None = None
    exp_year: int | None = None
    holder_name: str | None = None
    is_default: bool
    is_expired: bool = False
    created_at: datetime


class SavedMethodListResponse(BaseModel):
    items: list[SavedMethodResponse]
    total: int
