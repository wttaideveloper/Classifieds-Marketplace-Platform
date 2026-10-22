from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.currency_schema import Money


class SettlementConfigBody(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"schedule": "weekly", "weekday": 0, "currency": "INR", "min_payout": "500",
                                                              "payout_account": {"bank_name": "HDFC Bank", "account_last4": "4321", "holder": "Calm Clinic Pvt Ltd"}}})

    schedule: Literal["daily", "weekly", "monthly"] | None = None
    weekday: int | None = Field(None, ge=0, le=6, description="Weekly: 0 = Monday")
    month_day: int | None = Field(None, ge=1, le=28, description="Monthly: day of the month, 1 to 28")
    currency: str | None = Field(None, description="The currency the business is paid out in")
    min_payout: Decimal | None = Field(None, ge=0, description="Smaller totals wait for the next run")
    payout_account: dict[str, Any] | None = Field(None, description="Masked details only: bank name, last four digits, holder. Never a full account number")
    # platform only
    commission_percent: Decimal | None = Field(None, ge=0, le=100, description="Platform only. Empty = the platform default")
    fixed_fee: Decimal | None = Field(None, ge=0, description="Platform only. Charged per payment, in the payout currency")
    hold: bool | None = Field(None, description="Platform only. True stops settlements for this business")

    @field_validator("currency")
    @classmethod
    def _cur(cls, value):
        return value.strip().upper() if value else value

    @field_validator("payout_account")
    @classmethod
    def _masked(cls, value):
        if value is None:
            return value
        for key, item in value.items():
            digits = "".join(ch for ch in str(item) if ch.isdigit())
            if len(digits) > 6 and key.lower() not in ("ifsc", "swift", "routing"):
                raise ValueError(f"Send only the last four digits in '{key}'. A full account number is never stored here")
        return value


class SettlementConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: UUID
    schedule: str
    weekday: int
    month_day: int
    currency: str
    commission_percent: Money | None = None
    effective_commission_percent: Money | None = None
    fixed_fee: Money
    min_payout: Money
    hold: bool
    payout_account: dict | None = None
    last_run_at: datetime | None = None
    updated_at: datetime | None = None


class SettlementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    reference: str
    tenant_id: UUID
    status: str
    period_start: datetime
    period_end: datetime
    currency: str
    base_currency: str
    exchange_rate: Money = Field(..., description="1 base currency = this many payout currency, when it was made")
    gross_amount: Money
    refund_amount: Money
    commission_amount: Money
    fee_amount: Money
    fixed_fee_amount: Money
    tax_collected: Money
    net_amount: Money
    items_count: int
    payout_reference: str | None = None
    paid_at: datetime | None = None
    failure_reason: str | None = None
    created_at: datetime
    actions: list[str] = Field(default_factory=list)


class SettlementItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    payment_id: UUID | None = None
    refund_id: UUID | None = None
    charge_currency: str
    charge_amount: Money
    gross: Money
    commission: Money
    fee: Money
    fixed_fee: Money
    net: Money


class SettlementDetailResponse(SettlementResponse):
    items: list[SettlementItemResponse]


class SettlementListResponse(BaseModel):
    items: list[SettlementResponse]
    total: int
    page: int
    page_size: int


class SettlementRunRequest(BaseModel):
    tenant_id: UUID | None = Field(None, description="One business. Leave out to settle every business that is due")
    force: bool = Field(False, description="Settle now whatever the schedule, the minimum payout or a hold says (tenant_id needed)")
    dry_run: bool = Field(False, description="Show what would be settled without saving (tenant_id needed)")


class PayoutBody(BaseModel):
    payout_reference: str = Field(..., min_length=3, max_length=120, description="The bank / payout reference of the transfer")


class ReasonBody(BaseModel):
    reason: str = Field(..., min_length=3, max_length=255)


class GatewayReportRow(BaseModel):
    gateway_ref: str = Field(..., description="The gateway's payment id (or refund id when type is refund)")
    type: Literal["payment", "refund"] = "payment"
    amount: Decimal | None = None
    currency: str | None = None
    status: str | None = Field(None, description="captured / succeeded / failed ...")
    fee: Decimal | None = None


class ReconcileRequest(BaseModel):
    provider: str | None = Field(None, description="Defaults to the gateway in use")
    period_start: datetime | None = None
    period_end: datetime | None = Field(None, description="With period_start, also finds payments of ours that the report is missing")
    rows: list[GatewayReportRow] = Field(..., min_length=1, max_length=20000)


class ReconItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    issue: str
    gateway_ref: str | None = None
    payment_id: UUID | None = None
    gateway_amount: Money | None = None
    internal_amount: Money | None = None
    currency: str | None = None
    gateway_status: str | None = None
    internal_status: str | None = None
    detail: dict | None = None
    resolved: bool
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    note: str | None = None


class ReconRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    provider: str | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    total_rows: int
    matched: int
    mismatched: int
    created_by: str | None = None
    created_at: datetime
    items: list[ReconItemResponse] | None = None


class ReconRunListResponse(BaseModel):
    items: list[ReconRunResponse]
    total: int


class ResolveBody(BaseModel):
    note: str = Field(..., min_length=3, max_length=255)


class AuditEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_id: str | None = None
    actor_name: str | None = None
    actor_role: str | None = None
    action: str
    entity_type: str
    entity_id: str | None = None
    tenant_id: UUID | None = None
    before: dict | None = None
    after: dict | None = None
    reason: str | None = None
    created_at: datetime


class AuditListResponse(BaseModel):
    items: list[AuditEntryResponse]
    total: int
    page: int
    page_size: int
