import re
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.currency_schema import Money


class InvoiceLine(BaseModel):
    description: str
    category: str | None = None
    quantity: int = 1
    unit_price: Money
    net: Money
    tax: Money
    gross: Money
    taxes: list[dict] = Field(default_factory=list)


class InvoiceTaxRow(BaseModel):
    tax_type: str | None = None
    name: str
    rate: Money
    taxable_amount: Money
    tax_amount: Money


class InvoiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    doc_type: str
    number: str
    status: str
    tenant_id: UUID | None = None
    customer_user_id: UUID | None = None
    payment_id: UUID
    refund_id: UUID | None = None
    parent_invoice_id: UUID | None = None
    currency: str = Field(..., description="The currency the customer paid in; every amount below is in it")
    item_currency: str
    exchange_rate: Money = Field(..., description="1 item currency = this many invoice currency, as used for the payment")
    base_currency: str
    base_rate: Money
    base_total: Money
    subtotal: Money
    tax_total: Money
    total: Money
    lines: list[InvoiceLine] = Field(default_factory=list)
    tax_breakdown: list[InvoiceTaxRow] = Field(default_factory=list)
    billing: dict | None = None
    supplier: dict | None = None
    notes: str | None = None
    reason: str | None = None
    issued_at: datetime
    emailed_at: datetime | None = None
    email_count: int = 0
    download_path: str | None = None


class InvoiceListResponse(BaseModel):
    items: list[InvoiceResponse]
    total: int
    page: int
    page_size: int


class DebitNoteCreate(BaseModel):
    subtotal: Decimal = Field(..., ge=0, description="The extra amount before tax")
    tax_amount: Decimal = Field(Decimal(0), ge=0)
    reason: str = Field(..., min_length=3, max_length=255)


class TemplateBody(BaseModel):
    display_name: str | None = Field(None, max_length=255)
    logo_url: str | None = Field(None, max_length=500)
    address: str | None = Field(None, max_length=1000)
    footer_text: str | None = Field(None, max_length=1000)
    terms: str | None = Field(None, max_length=2000)
    number_prefix: str | None = Field(None, description="2 to 6 capital letters or digits; used for new invoices only")
    locale: str | None = Field(None, max_length=10, description="en-IN groups numbers 12,34,567.89; others 1,234,567.89")
    accent_color: str | None = Field(None, description="#rrggbb")
    show_exchange_rate: bool | None = None

    @field_validator("number_prefix")
    @classmethod
    def _prefix(cls, value):
        if value is None:
            return value
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{2,6}", value):
            raise ValueError("2 to 6 capital letters or digits")
        if value in ("CN", "DN", "SET"):
            raise ValueError("CN, DN and SET are used for credit notes, debit notes and settlements")
        return value

    @field_validator("accent_color")
    @classmethod
    def _color(cls, value):
        if value is not None and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("a colour like #1f3a5f")
        return value

    @field_validator("logo_url")
    @classmethod
    def _logo(cls, value):
        if value and not (value.startswith("http://") or value.startswith("https://") or value.startswith("/")):
            raise ValueError("an http(s) address or a path on this site")
        return value


class TemplateResponse(BaseModel):
    tenant_id: UUID | None = None
    is_default: bool = Field(False, description="True when this is the platform's wording and the business has none of its own")
    display_name: str | None = None
    logo_url: str | None = None
    address: str | None = None
    footer_text: str | None = None
    terms: str | None = None
    number_prefix: str = "INV"
    locale: str = "en-IN"
    accent_color: str = "#1f3a5f"
    show_exchange_rate: bool = True
