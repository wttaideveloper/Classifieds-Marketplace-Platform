from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

# Money and rates travel as strings in JSON ("1180.00"), so no digit is lost to floating point.
Money = Annotated[Decimal, PlainSerializer(lambda v: format(v, "f"), return_type=str, when_used="json")]


class CurrencyBase(BaseModel):
    @field_validator("code", check_fields=False)
    @classmethod
    def _upper(cls, value):
        value = (value or "").strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise ValueError("a currency code is three letters, for example INR")
        return value


class CurrencyCreate(CurrencyBase):
    code: str
    name: str | None = Field(None, max_length=100, description="Defaults to the ISO name")
    symbol: str | None = Field(None, max_length=10)
    decimals: int | None = Field(None, ge=0, le=4, description="Defaults to the usual precision (JPY 0, KWD 3, others 2)")
    is_active: bool = True
    min_amount: Decimal | None = Field(None, ge=0)
    max_amount: Decimal | None = Field(None, gt=0)


class CurrencyUpdate(BaseModel):
    name: str | None = Field(None, max_length=100)
    symbol: str | None = Field(None, max_length=10)
    is_active: bool | None = None
    min_amount: Decimal | None = Field(None, ge=0)
    max_amount: Decimal | None = Field(None, gt=0)
    sort_order: int | None = None
    clear_limits: bool = Field(False, description="True removes the minimum and maximum")


class CurrencyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    symbol: str | None = None
    decimals: int
    is_active: bool
    is_base: bool = False
    min_amount: Money | None = None
    max_amount: Money | None = None
    sort_order: int = 0


class CurrencyListResponse(BaseModel):
    base_currency: str
    items: list[CurrencyResponse]
    total: int


class RateResponse(BaseModel):
    base_currency: str
    quote_currency: str
    rate: Money
    source: str
    pinned: bool = False
    updated_at: datetime | None = None


class RateListResponse(BaseModel):
    base_currency: str
    items: list[RateResponse]
    total: int


class ManualRate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"quote_currency": "USD", "rate": "0.0119", "pinned": True, "note": "Treasury rate for October"}})

    quote_currency: str
    rate: Decimal = Field(..., gt=0, description="How many units of the quote currency one unit of the base currency buys")
    pinned: bool = Field(False, description="A pinned rate is not replaced by the automatic refresh")
    note: str | None = Field(None, max_length=255)

    @field_validator("quote_currency")
    @classmethod
    def _upper(cls, value):
        return value.strip().upper()


class RefreshResult(BaseModel):
    base_currency: str
    updated: list[str]
    skipped_pinned: list[str]
    missing: list[str]
    provider: str


class RateHistoryEntry(BaseModel):
    id: UUID
    base_currency: str
    quote_currency: str
    rate: Money
    source: str
    provider: str | None = None
    pinned: bool = False
    created_by: str | None = None
    note: str | None = None
    created_at: datetime


class RateHistoryResponse(BaseModel):
    items: list[RateHistoryEntry]
    total: int


class ConversionResponse(BaseModel):
    amount: Money
    from_currency: str
    to_currency: str
    rate: Money
    converted: Money
    rate_updated_at: datetime | None = None


class PreferenceBody(BaseModel):
    currency: str

    @field_validator("currency")
    @classmethod
    def _upper(cls, value):
        return value.strip().upper()


class PreferenceResponse(BaseModel):
    currency: str
    is_default: bool = Field(False, description="True when the user has not chosen one and this is the platform's base currency")
