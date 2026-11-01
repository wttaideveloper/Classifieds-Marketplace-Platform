from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.payment_access import PayActor, require_merchant_or_admin
from app.db.database import get_db
from app.schemas.payment_report_schema import (
    CommerceOverview, CurrencyReportOut, ExchangeRateReportOut, PaymentReportOut, ReconciliationReportOut, RefundReportOut, SettlementReportOut,
)
from app.services import finance_audit, payment_export, payment_overview, payment_report_service as svc

router = APIRouter(tags=["Payment Reports"])

Fmt = Literal["json", "csv"]


def _out(report: dict, fmt: str, columns: list[str], name: str, key: str = "rows"):
    return payment_export.csv_response(report[key], columns, f"{name}.csv") if fmt == "csv" else finance_audit.jsonable(report)


@router.get("/overview", response_model=CommerceOverview, summary="Commerce overview: payments, refunds, tax and settlements in one call",
            description="The dashboard numbers. A business sees its own, the Super Admin all (`tenant_id` narrows to one business). Money is in the base currency so currencies can be added up. "
                        "`date_from` / `date_to` limit what is counted by creation time; leave them out for all time. `needs_attention` is what a person should look at: payments under "
                        "review or needing reconciliation, overdue or failed refunds, failed or held settlements and (Super Admin) open reconciliation differences.")
def overview(tenant_id: UUID | None = Query(None, description="Super Admin only"), date_from: datetime | None = Query(None), date_to: datetime | None = Query(None),
             db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    return payment_overview.overview(db, actor, tenant_id=tenant_id, date_from=date_from, date_to=date_to)


@router.get("/payments", summary="Payment report", responses={200: {"model": PaymentReportOut, "description": "One row per payment, newest first, with totals for the whole filter."}},
            description="Every payment with its amounts in the item, charge and base currency, the rate, the fee and the invoice number." + """ `format=csv` returns the same rows as a CSV file instead (Content-Type text/csv). `status`, `currency`, `tenant_id` (Super Admin) and the dates filter it; `page` / `page_size` (max 1000) page the rows, the totals cover every match.""")
def payment_report(tenant_id: UUID | None = Query(None, description="Super Admin only"), date_from: datetime | None = Query(None), date_to: datetime | None = Query(None),
                   status: str | None = Query(None), currency: str | None = Query(None, min_length=3, max_length=3), format: Fmt = Query("json"),
                   page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=1000), db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    report = svc.payment_report(db, actor, tenant_id=tenant_id, date_from=date_from, date_to=date_to, status=status, currency=currency, page=page, page_size=page_size)
    return _out(report, format, ["payment_id", "created_at", "captured_at", "status", "tenant_id", "customer_email", "purchase_type", "purchase_id", "attempt_no", "provider",
                                 "item_currency", "item_total", "charge_currency", "charge_subtotal", "charge_tax", "charge_total", "exchange_rate", "base_currency", "base_total",
                                 "gateway_fee", "refunded_amount", "method", "failure_code", "invoice_number"], "payment-report")


@router.get("/refunds", summary="Refund report", responses={200: {"model": RefundReportOut, "description": "One row per refund, newest first."}},
            description="Every refund with its status, destination, turnaround time and whether it was escalated." + """ `format=csv` returns a CSV file. Filters: `status`, `tenant_id` (Super Admin), the dates, `page`, `page_size`.""")
def refund_report(tenant_id: UUID | None = Query(None, description="Super Admin only"), date_from: datetime | None = Query(None), date_to: datetime | None = Query(None),
                  status: str | None = Query(None), format: Fmt = Query("json"), page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=1000),
                  db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    report = svc.refund_report(db, actor, tenant_id=tenant_id, date_from=date_from, date_to=date_to, status=status, page=page, page_size=page_size)
    return _out(report, format, ["refund_id", "payment_id", "created_at", "approved_at", "completed_at", "status", "refund_type", "destination", "currency", "amount", "tax_refunded",
                                 "base_amount", "reason", "requested_by_role", "sla_due_at", "escalated", "turnaround_hours"], "refund-report")


@router.get("/settlements", summary="Settlement report", responses={200: {"model": SettlementReportOut, "description": "One row per settlement, newest first, with totals by payout currency."}},
            description="Every payout with its gross, refunds, commission, fees and net, and totals by currency." + """ `format=csv` returns a CSV file. Filters: `status`, `tenant_id` (Super Admin), the dates, `page`, `page_size`.""")
def settlement_report(tenant_id: UUID | None = Query(None, description="Super Admin only"), date_from: datetime | None = Query(None), date_to: datetime | None = Query(None),
                      status: str | None = Query(None), format: Fmt = Query("json"), page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=1000),
                      db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    report = svc.settlement_report(db, actor, tenant_id=tenant_id, date_from=date_from, date_to=date_to, status=status, page=page, page_size=page_size)
    return _out(report, format, ["settlement_id", "reference", "tenant_id", "status", "currency", "period_start", "period_end", "gross_amount", "refund_amount", "commission_amount",
                                 "fee_amount", "fixed_fee_amount", "tax_collected", "net_amount", "items_count", "paid_at", "payout_reference"], "settlement-report")


@router.get("/currency", summary="Currency report", responses={200: {"model": CurrencyReportOut, "description": "One row per currency customers paid in."}},
            description="Paid payments grouped by the currency customers paid in, with their value in the base currency." + """ `format=csv` returns a CSV file. Filters: `tenant_id` (Super Admin) and the dates.""")
def currency_report(tenant_id: UUID | None = Query(None, description="Super Admin only"), date_from: datetime | None = Query(None), date_to: datetime | None = Query(None),
                    format: Fmt = Query("json"), db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    report = svc.currency_report(db, actor, tenant_id=tenant_id, date_from=date_from, date_to=date_to)
    return _out(report, format, ["currency", "payments", "charge_total", "tax", "refunded", "base_total", "rate_min", "rate_max"], "currency-report")


@router.get("/exchange-rates", summary="Exchange rate report (Super Admin)", responses={200: {"model": ExchangeRateReportOut, "description": "A summary per currency and the rate history behind it."}},
            description="The rate history with the low, high and average of each currency in the window." + """ `format=csv` returns the history as a CSV file. Filters: `quote` (one currency) and the dates.""")
def exchange_rate_report(quote: str | None = Query(None, min_length=3, max_length=3), date_from: datetime | None = Query(None), date_to: datetime | None = Query(None),
                         format: Fmt = Query("json"), db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    report = svc.exchange_rate_report(db, actor, quote=quote, date_from=date_from, date_to=date_to)
    return _out(report, format, ["quote_currency", "base_currency", "rate", "source", "pinned", "created_by", "created_at", "note"], "exchange-rate-report")


@router.get("/reconciliation", summary="Reconciliation report (Super Admin)", responses={200: {"model": ReconciliationReportOut, "description": "The runs, and the differences found by kind."}},
            description="The reconciliation runs, and the mismatches found by kind, open and resolved." + """ `format=csv` returns the runs as a CSV file. Filters: the dates. The differences themselves are listed and resolved under /finance/reconciliation.""")
def reconciliation_report(date_from: datetime | None = Query(None), date_to: datetime | None = Query(None), format: Fmt = Query("json"),
                          db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    report = svc.reconciliation_report(db, actor, date_from=date_from, date_to=date_to)
    return _out(report, format, ["run_id", "provider", "created_at", "period_start", "period_end", "rows", "matched", "mismatched"], "reconciliation-report")
