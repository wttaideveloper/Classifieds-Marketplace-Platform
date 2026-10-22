from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.currency_schema import Money

TaxType = Literal["gst", "vat", "sales_tax", "service_tax"]
Rounding = Literal["half_up", "half_even", "up", "down"]
Condition = Literal["any", "intra_state", "inter_state"]
IdType = Literal["gstin", "vat", "exemption_certificate"]


def _country(value: str) -> str:
    value = (value or "").strip().upper()
    if len(value) != 2 or not value.isalpha():
        raise ValueError("a country is its two-letter code, for example IN")
    return value


class TaxRuleBody(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "tax_type": "gst", "name": "CGST", "country": "IN", "state": None, "rate": "9", "applies_to": "service",
        "condition": "intra_state", "rounding_mode": "half_up", "reverse_charge_allowed": False, "priority": 10}})

    tax_type: TaxType
    name: str = Field(..., min_length=1, max_length=60, description="What appears on the invoice: CGST, SGST, IGST, VAT ...")
    country: str
    state: str | None = Field(None, max_length=60, description="Leave empty for a rule that covers the whole country")
    rate: Decimal = Field(..., ge=0, le=100, description="Percent, for example 18")
    applies_to: str = Field("all", max_length=30, description="all, or the kind of item: service, product, event, training ...")
    condition: Condition = Field("any", description="intra_state: only when the merchant and the customer are in the same state. inter_state: otherwise")
    rounding_mode: Rounding = "half_up"
    reverse_charge_allowed: bool = Field(False, description="The tax is not charged to a business customer abroad who is registered (reverse charge)")
    priority: int = Field(100, ge=0, le=10000)
    valid_from: datetime | None = Field(None, description="When the rule starts. Defaults to now. A later date schedules a change")
    valid_to: datetime | None = None
    notes: str | None = Field(None, max_length=255)

    @field_validator("country")
    @classmethod
    def _c(cls, value):
        return _country(value)

    @field_validator("applies_to")
    @classmethod
    def _applies(cls, value):
        return (value or "all").strip().lower() or "all"


class TaxRuleChange(BaseModel):
    """A change adds the next version of the rule; the old version keeps applying to anything before `valid_from`."""
    name: str | None = Field(None, min_length=1, max_length=60)
    state: str | None = Field(None, max_length=60)
    clear_state: bool = False
    rate: Decimal | None = Field(None, ge=0, le=100)
    applies_to: str | None = Field(None, max_length=30)
    condition: Condition | None = None
    rounding_mode: Rounding | None = None
    reverse_charge_allowed: bool | None = None
    priority: int | None = Field(None, ge=0, le=10000)
    valid_from: datetime | None = Field(None, description="When the new version starts. Defaults to now")
    valid_to: datetime | None = None
    notes: str | None = Field(None, max_length=255)
    reason: str | None = Field(None, max_length=500, description="Why it changed, kept in the audit log")


class TaxRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    rule_key: UUID
    version: int
    tax_type: str
    name: str
    country: str
    state: str | None = None
    rate: Money
    applies_to: str
    condition: str
    rounding_mode: str
    reverse_charge_allowed: bool
    priority: int
    valid_from: datetime
    valid_to: datetime | None = None
    is_active: bool
    is_current: bool = False
    supersedes_id: UUID | None = None
    notes: str | None = None
    created_by: str | None = None
    created_at: datetime


class TaxRuleListResponse(BaseModel):
    items: list[TaxRuleResponse]
    total: int


class TaxItem(BaseModel):
    description: str = Field("", max_length=255)
    amount: Decimal = Field(..., ge=0, description="The price of one unit")
    quantity: int = Field(1, ge=1, le=100000)
    category: str = Field("service", max_length=30)


class TaxCustomer(BaseModel):
    country: str
    state: str | None = Field(None, max_length=60)
    tax_id: str | None = Field(None, max_length=60, description="The customer's GSTIN / VAT number, when they are a business")
    is_business: bool = False

    @field_validator("country")
    @classmethod
    def _c(cls, value):
        return _country(value)


class TaxCalcRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "currency": "INR", "items": [{"description": "Consultation", "amount": "1000", "category": "service"}],
        "customer": {"country": "IN", "state": "Karnataka"}, "merchant_tenant_id": None, "prices_include_tax": False}})

    currency: str
    items: list[TaxItem] = Field(..., min_length=1, max_length=100)
    customer: TaxCustomer
    merchant_tenant_id: UUID | None = Field(None, description="The merchant, so their state and registration are used")
    prices_include_tax: bool = False

    @field_validator("currency")
    @classmethod
    def _cur(cls, value):
        return value.strip().upper()


class TaxComponent(BaseModel):
    rule_id: UUID | None = None
    rule_version: int | None = None
    tax_type: str
    name: str
    rate: Money
    taxable_amount: Money
    tax_amount: Money
    reverse_charge: bool = False


class TaxLineResult(BaseModel):
    ref: str
    description: str
    category: str
    quantity: int = 1
    unit_amount: Money
    net_amount: Money
    tax_amount: Money
    gross_amount: Money
    taxes: list[TaxComponent]


class TaxBreakdownRow(BaseModel):
    tax_type: str
    name: str
    rate: Money
    country: str
    state: str | None = None
    taxable_amount: Money
    tax_amount: Money


class TaxCalcResponse(BaseModel):
    currency: str
    prices_include_tax: bool
    subtotal: Money
    tax_total: Money
    total: Money
    is_exempt: bool = False
    exempt_reason: str | None = None
    reverse_charge: bool = False
    note: str | None = None
    warnings: list[str] = Field(default_factory=list)
    jurisdiction: dict
    lines: list[TaxLineResult]
    breakdown: list[TaxBreakdownRow]


class ValidateIdRequest(BaseModel):
    id_type: IdType
    number: str = Field(..., min_length=1, max_length=60)
    country: str | None = None
    state: str | None = Field(None, max_length=60)

    @field_validator("country")
    @classmethod
    def _c(cls, value):
        return _country(value) if value else value


class ValidateIdResponse(BaseModel):
    id_type: str
    number: str
    valid: bool
    checksum_ok: bool | None = None
    state: str | None = None
    message: str
    note: str = "This checks the format only. A person approves the number before it changes any tax."


class ExemptionCreate(BaseModel):
    id_type: IdType
    number: str = Field(..., min_length=1, max_length=60)
    country: str
    state: str | None = Field(None, max_length=60)
    legal_name: str | None = Field(None, max_length=255)
    valid_until: date | None = None

    @field_validator("country")
    @classmethod
    def _c(cls, value):
        return _country(value)


class ExemptionDecision(BaseModel):
    action: Literal["approve", "reject", "revoke"]
    note: str | None = Field(None, max_length=255)


class ExemptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    id_type: str
    number: str
    country: str
    state: str | None = None
    legal_name: str | None = None
    status: str
    format_valid: bool
    valid_until: date | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None
    created_at: datetime


class ExemptionListResponse(BaseModel):
    items: list[ExemptionResponse]
    total: int


class MerchantTaxProfileBody(BaseModel):
    country: str
    state: str | None = Field(None, max_length=60)
    tax_id: str | None = Field(None, max_length=40)
    tax_id_type: Literal["gstin", "vat", "other"] | None = None
    legal_name: str | None = Field(None, max_length=255)
    address: str | None = Field(None, max_length=1000)
    is_tax_registered: bool = True

    @field_validator("country")
    @classmethod
    def _c(cls, value):
        return _country(value)


class MerchantTaxProfileResponse(MerchantTaxProfileBody):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: UUID
    updated_at: datetime | None = None


class TaxTransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    payment_id: UUID
    refund_id: UUID | None = None
    invoice_id: UUID | None = None
    tenant_id: UUID | None = None
    tax_type: str
    name: str
    country: str
    state: str | None = None
    rate: Money
    taxable_amount: Money
    tax_amount: Money
    item_currency: str
    tax_amount_charge: Money
    charge_currency: str
    is_exempt: bool
    is_reverse_charge: bool
    created_at: datetime


class TaxHistoryResponse(BaseModel):
    items: list[TaxTransactionResponse]
    total: int
    page: int
    page_size: int
