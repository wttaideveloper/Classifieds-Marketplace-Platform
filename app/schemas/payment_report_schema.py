"""Typed shapes (and sample responses) of the finance reports, the Commerce overview and the gateway status.

The reports return plain dictionaries (so a CSV can be built from the same rows); these models only describe them for Swagger and the web
developers. Amounts are strings in JSON ("1180.00", the decimals of their currency), times are UTC without an offset.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.currency_schema import Money


class _Doc(BaseModel):
    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------- payment report

class PaymentReportRow(_Doc):
    payment_id: str
    created_at: datetime
    captured_at: datetime | None = None
    status: str
    tenant_id: str | None = None
    customer_email: str | None = None
    purchase_type: Literal["cart_order", "event_order", "custom"]
    purchase_id: str
    attempt_no: int
    provider: str
    item_currency: str
    item_total: str
    charge_currency: str
    charge_subtotal: str
    charge_tax: str
    charge_total: str
    exchange_rate: str = Field(..., description="1 item currency = this many charge currency, locked when the payment was created")
    base_currency: str
    base_total: str
    gateway_fee: str | None = None
    refunded_amount: str
    method: str | None = Field(None, description='e.g. "visa 4242"')
    failure_code: str | None = None
    invoice_number: str | None = None


class PaymentReportTotals(_Doc):
    payments: int
    paid: int
    failed: int
    base_collected: str
    base_refunded: str
    base_tax: str


class PaymentReportOut(_Doc):
    model_config = ConfigDict(extra="allow", json_schema_extra={"example": {
        "base_currency": "INR", "total_rows": 1, "page": 1, "page_size": 100,
        "rows": [{"payment_id": "6c1f…", "created_at": "2026-10-28T10:00:00", "captured_at": "2026-10-28T10:00:07", "status": "succeeded", "tenant_id": "a1b2…",
                  "customer_email": "priya@example.com", "purchase_type": "cart_order", "purchase_id": "3f2a…", "attempt_no": 1, "provider": "stripe",
                  "item_currency": "INR", "item_total": "1180.00", "charge_currency": "USD", "charge_subtotal": "12.00", "charge_tax": "2.16", "charge_total": "14.16",
                  "exchange_rate": "0.0120000000", "base_currency": "INR", "base_total": "1180.00", "gateway_fee": "0.71", "refunded_amount": "0.00",
                  "method": "visa 4242", "failure_code": None, "invoice_number": "INV-2026-000001"}],
        "totals": {"payments": 1, "paid": 1, "failed": 0, "base_collected": "1180.00", "base_refunded": "0.00", "base_tax": "180.00"}}})
    base_currency: str
    rows: list[PaymentReportRow]
    total_rows: int
    page: int
    page_size: int
    totals: PaymentReportTotals


# ---------------------------------------------------------------- refund report

class RefundReportRow(_Doc):
    refund_id: str
    payment_id: str
    created_at: datetime
    approved_at: datetime | None = None
    completed_at: datetime | None = None
    status: Literal["requested", "approved", "processing", "completed", "failed", "rejected"]
    refund_type: Literal["full", "partial"]
    destination: Literal["original", "bank_transfer", "wallet"]
    currency: str
    amount: str
    tax_refunded: str
    base_amount: str
    reason: str | None = None
    requested_by_role: str | None = None
    sla_due_at: datetime | None = None
    escalated: bool
    turnaround_hours: float | None = Field(None, description="From request to completion. null until completed")


class RefundReportTotals(_Doc):
    refunds: int
    completed: int
    open: int
    escalated: int
    base_refunded: str


class RefundReportOut(_Doc):
    model_config = ConfigDict(extra="allow", json_schema_extra={"example": {
        "base_currency": "INR", "total_rows": 1, "page": 1, "page_size": 100,
        "rows": [{"refund_id": "9d2e…", "payment_id": "6c1f…", "created_at": "2026-10-29T09:00:00", "approved_at": "2026-10-29T09:20:00", "completed_at": "2026-10-29T09:21:00",
                  "status": "completed", "refund_type": "partial", "destination": "original", "currency": "USD", "amount": "5.00", "tax_refunded": "0.76", "base_amount": "416.67",
                  "reason": "Damaged item", "requested_by_role": "customer", "sla_due_at": "2026-11-01T09:20:00", "escalated": False, "turnaround_hours": 0.4}],
        "totals": {"refunds": 1, "completed": 1, "open": 0, "escalated": 0, "base_refunded": "416.67"}}})
    base_currency: str
    rows: list[RefundReportRow]
    total_rows: int
    page: int
    page_size: int
    totals: RefundReportTotals


# ---------------------------------------------------------------- settlement report

class SettlementReportRow(_Doc):
    settlement_id: str
    reference: str
    tenant_id: str
    status: Literal["pending", "paid", "failed", "on_hold", "cancelled"]
    currency: str
    period_start: datetime
    period_end: datetime
    gross_amount: str
    refund_amount: str
    commission_amount: str
    fee_amount: str = Field(..., description="Gateway fees")
    fixed_fee_amount: str
    tax_collected: str = Field(..., description="Included in gross, for information")
    net_amount: str
    items_count: int
    paid_at: datetime | None = None
    payout_reference: str | None = None


class SettlementCurrencyTotal(_Doc):
    settlements: int
    net: str
    paid: str
    pending: str


class SettlementReportOut(_Doc):
    model_config = ConfigDict(extra="allow", json_schema_extra={"example": {
        "total_rows": 1, "page": 1, "page_size": 100,
        "rows": [{"settlement_id": "c7a0…", "reference": "STL-2026-000001", "tenant_id": "a1b2…", "status": "paid", "currency": "INR", "period_start": "2026-10-01T00:00:00",
                  "period_end": "2026-10-31T23:59:59", "gross_amount": "11800.00", "refund_amount": "1180.00", "commission_amount": "900.00", "fee_amount": "236.00",
                  "fixed_fee_amount": "0.00", "tax_collected": "1800.00", "net_amount": "9484.00", "items_count": 14, "paid_at": "2026-11-02T08:00:00", "payout_reference": "UTR123456"}],
        "totals": {"settlements": 1, "by_currency": {"INR": {"settlements": 1, "net": "9484.00", "paid": "9484.00", "pending": "0"}}}}})
    rows: list[SettlementReportRow]
    total_rows: int
    page: int
    page_size: int
    totals: dict = Field(..., description='{"settlements": n, "by_currency": {"INR": SettlementCurrencyTotal}}')


# ---------------------------------------------------------------- currency report

class CurrencyReportRow(_Doc):
    currency: str
    payments: int
    charge_total: str
    tax: str
    refunded: str
    base_total: str
    rate_min: str
    rate_max: str


class CurrencyReportOut(_Doc):
    model_config = ConfigDict(extra="allow", json_schema_extra={"example": {
        "base_currency": "INR",
        "rows": [{"currency": "INR", "payments": 12, "charge_total": "14160.00", "tax": "2160.00", "refunded": "0.00", "base_total": "14160.00", "rate_min": "1", "rate_max": "1"},
                 {"currency": "USD", "payments": 3, "charge_total": "42.48", "tax": "6.48", "refunded": "5.00", "base_total": "3540.00", "rate_min": "83.1", "rate_max": "83.6"}],
        "totals": {"payments": 15, "base_total": "17700.00"}}})
    base_currency: str
    rows: list[CurrencyReportRow]
    totals: dict


# ---------------------------------------------------------------- exchange-rate report (Super Admin)

class ExchangeRateSummary(_Doc):
    currency: str
    changes: int
    low: str
    high: str
    average: str


class ExchangeRateReportOut(_Doc):
    model_config = ConfigDict(extra="allow", json_schema_extra={"example": {
        "base_currency": "INR", "total_rows": 2,
        "summary": [{"currency": "USD", "changes": 2, "low": "0.0119000000", "high": "0.0121000000", "average": "0.0120000000"}],
        "rows": [{"quote_currency": "USD", "base_currency": "INR", "rate": "0.0121000000", "source": "frankfurter", "pinned": False, "created_by": None,
                  "created_at": "2026-10-29T06:00:00", "note": None}]}})
    base_currency: str
    summary: list[ExchangeRateSummary]
    rows: list[dict]
    total_rows: int


# ---------------------------------------------------------------- reconciliation report (Super Admin)

class ReconciliationReportOut(_Doc):
    model_config = ConfigDict(extra="allow", json_schema_extra={"example": {
        "rows": [{"run_id": "e41b…", "provider": "stripe", "created_at": "2026-11-01T07:00:00", "period_start": "2026-10-01T00:00:00", "period_end": "2026-10-31T23:59:59",
                  "rows": 120, "matched": 118, "mismatched": 2}],
        "by_issue": [{"issue": "amount_mismatch", "items": 1, "open": 1}, {"issue": "missing_internal", "items": 1, "open": 0}],
        "totals": {"runs": 1, "items": 2, "open": 1}}})
    rows: list[dict]
    by_issue: list[dict] = Field(..., description="Per kind of difference: amount_mismatch, status_mismatch, fee_mismatch, currency_mismatch, missing_internal, missing_gateway, duplicate")
    totals: dict


# ---------------------------------------------------------------- tax report

class TaxReportOut(_Doc):
    model_config = ConfigDict(extra="allow", json_schema_extra={"example": {
        "group_by": "tax_type", "base_currency": "INR", "total_tax_base": "1800.00",
        "rows": [{"tax_type": "gst", "transactions": 20, "taxable_amount": {"INR": "10000.00"}, "tax_amount": {"INR": "1800.00"}, "tax_amount_base": "1800.00",
                  "reverse_charge_taxable": {}, "exempt_taxable": {}}]}})
    group_by: Literal["tax_type", "country", "state", "name", "month", "day"]
    base_currency: str
    rows: list[dict] = Field(..., description="Each row has the group_by field (named after it), transactions, taxable_amount and tax_amount per item currency, tax_amount_base, reverse_charge_taxable and exempt_taxable")
    total_tax_base: str


# ---------------------------------------------------------------- Commerce overview

class OverviewPeriod(BaseModel):
    date_from: datetime | None = None
    date_to: datetime | None = None


class OverviewPayments(BaseModel):
    total: int
    paid: int = Field(..., description="succeeded, partially_refunded and refunded")
    waiting: int = Field(..., description="created, requires_action, processing and authorized")
    failed: int
    under_review: int
    closed_unpaid: int = Field(..., description="cancelled and expired")
    by_status: dict[str, int]
    base_collected: Money = Field(..., description="Paid payments, in the base currency")
    base_refunded: Money
    base_net: Money
    success_rate_percent: float | None = Field(None, description="paid / total. null when there are no payments")


class OverviewRefunds(BaseModel):
    total: int
    open: int = Field(..., description="requested, approved and processing")
    completed: int
    failed: int
    rejected: int
    overdue: int = Field(..., description="open and past the target time")
    escalated: int
    by_status: dict[str, int]
    base_refunded: Money = Field(..., description="Completed refunds, in the base currency")
    base_open_amount: Money


class OverviewTax(BaseModel):
    base_collected: Money
    base_refunded: Money
    base_net: Money


class OverviewSettlementCurrency(BaseModel):
    currency: str
    pending_net: Money
    paid_net: Money


class OverviewAwaiting(BaseModel):
    payments: int
    base_amount: Money


class OverviewSettlements(BaseModel):
    total: int
    pending: int
    paid: int
    failed: int
    on_hold: int
    by_status: dict[str, int]
    by_currency: list[OverviewSettlementCurrency]
    awaiting_settlement: OverviewAwaiting = Field(..., description="Paid payments not yet in any settlement")


class OverviewAttention(BaseModel):
    payments_under_review: int
    payments_needing_reconciliation: int
    refunds_overdue: int
    refunds_failed: int
    settlements_failed: int
    settlements_on_hold: int
    open_reconciliation_mismatches: int | None = Field(None, description="Super Admin only; null for a business")


class CommerceOverview(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "base_currency": "INR", "period": {"date_from": "2026-10-01T00:00:00", "date_to": None},
        "payments": {"total": 40, "paid": 31, "waiting": 3, "failed": 4, "under_review": 1, "closed_unpaid": 1,
                     "by_status": {"succeeded": 28, "partially_refunded": 2, "refunded": 1, "failed": 4, "review": 1, "created": 2, "processing": 1, "expired": 1},
                     "base_collected": "36580.00", "base_refunded": "1180.00", "base_net": "35400.00", "success_rate_percent": 77.5},
        "refunds": {"total": 5, "open": 2, "completed": 2, "failed": 0, "rejected": 1, "overdue": 1, "escalated": 1,
                    "by_status": {"requested": 1, "approved": 0, "processing": 1, "completed": 2, "failed": 0, "rejected": 1},
                    "base_refunded": "1180.00", "base_open_amount": "800.00"},
        "tax": {"base_collected": "5580.00", "base_refunded": "180.00", "base_net": "5400.00"},
        "settlements": {"total": 3, "pending": 1, "paid": 2, "failed": 0, "on_hold": 0, "by_status": {"pending": 1, "paid": 2},
                        "by_currency": [{"currency": "INR", "pending_net": "9484.00", "paid_net": "18000.00"}],
                        "awaiting_settlement": {"payments": 6, "base_amount": "7080.00"}},
        "needs_attention": {"payments_under_review": 1, "payments_needing_reconciliation": 0, "refunds_overdue": 1, "refunds_failed": 0, "settlements_failed": 0,
                            "settlements_on_hold": 0, "open_reconciliation_mismatches": 2}}})
    base_currency: str
    period: OverviewPeriod
    payments: OverviewPayments
    refunds: OverviewRefunds
    tax: OverviewTax
    settlements: OverviewSettlements
    needs_attention: OverviewAttention


# ---------------------------------------------------------------- gateway status (read-only)

class GatewayProviderStatus(BaseModel):
    provider: Literal["razorpay", "stripe", "test"]
    label: str
    active: bool = Field(..., description="The gateway that takes payments now")
    configured: bool = Field(..., description="Its keys are set on the server")
    mode: Literal["test", "live", "unknown"] | None = Field(None, description="From the key's prefix. null when no key is set")
    webhook_secret_set: bool
    publishable_key_set: bool | None = Field(None, description="Stripe only")
    webhook_url: str = Field(..., description="Give this address to the gateway as its webhook")
    webhook_events: list[str]


class GatewayStatus(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "editable": False, "edit_note": "The gateway and its keys are set in the server's environment, not through the API. Ask the platform team to change them.",
        "active_gateway": "stripe", "active_mode": "test",
        "providers": [{"provider": "stripe", "label": "Stripe", "active": True, "configured": True, "mode": "test", "webhook_secret_set": True, "publishable_key_set": True,
                       "webhook_url": "https://api.example.com/api/v1/payments/webhook/stripe",
                       "webhook_events": ["payment_intent.succeeded", "payment_intent.payment_failed", "payment_intent.amount_capturable_updated", "payment_intent.canceled", "refund.updated"]}],
        "base_currency": "INR", "exchange_rates": {"source_url": "https://api.frankfurter.app/latest", "max_age_hours": 48, "rates_loaded": 5}, "tax": {"active_rules": 3},
        "jobs": {"enabled": True, "interval_seconds": 600}, "limits": {"max_attempts": 5, "stale_minutes": 30, "gateway_timeout_seconds": 20, "refund_sla_hours": 72},
        "risk": {"review_score": 50, "deny_score": 80, "high_amount_base": 200000.0, "fraud_service_set": False},
        "settlement_defaults": {"commission_percent": 10.0, "gateway_fee_percent": 2.0}, "warnings": ["A test key is set while the server is in production mode."]}})
    editable: bool = Field(..., description="Always false: the gateway is chosen and keyed in the server's environment")
    edit_note: str
    active_gateway: Literal["test", "razorpay", "stripe"]
    active_mode: Literal["test", "live", "unknown"] | None = None
    providers: list[GatewayProviderStatus]
    base_currency: str
    exchange_rates: dict
    tax: dict
    jobs: dict
    limits: dict
    risk: dict
    settlement_defaults: dict
    warnings: list[str] = Field(..., description="Things to fix before taking real payments. Empty when everything is set up")
