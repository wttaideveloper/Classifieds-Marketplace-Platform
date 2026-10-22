from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.payment_access import PayActor, require_merchant_or_admin
from app.db.database import get_db
from app.services import finance_audit, payment_export, payment_report_service as svc

router = APIRouter(tags=["Payment Reports"])

Fmt = Literal["json", "csv"]


def _out(report: dict, fmt: str, columns: list[str], name: str, key: str = "rows"):
    return payment_export.csv_response(report[key], columns, f"{name}.csv") if fmt == "csv" else finance_audit.jsonable(report)


@router.get("/payments", summary="Payment report", description="Every payment with its amounts in the item, charge and base currency, the rate, the fee and the invoice number. `format=csv` downloads it.")
def payment_report(tenant_id: UUID | None = Query(None, description="Super Admin only"), date_from: datetime | None = Query(None), date_to: datetime | None = Query(None),
                   status: str | None = Query(None), currency: str | None = Query(None, min_length=3, max_length=3), format: Fmt = Query("json"),
                   page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=1000), db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    report = svc.payment_report(db, actor, tenant_id=tenant_id, date_from=date_from, date_to=date_to, status=status, currency=currency, page=page, page_size=page_size)
    return _out(report, format, ["payment_id", "created_at", "captured_at", "status", "tenant_id", "customer_email", "purchase_type", "purchase_id", "attempt_no", "provider",
                                 "item_currency", "item_total", "charge_currency", "charge_subtotal", "charge_tax", "charge_total", "exchange_rate", "base_currency", "base_total",
                                 "gateway_fee", "refunded_amount", "method", "failure_code", "invoice_number"], "payment-report")


@router.get("/refunds", summary="Refund report", description="Every refund with its status, destination, turnaround time and whether it was escalated.")
def refund_report(tenant_id: UUID | None = Query(None, description="Super Admin only"), date_from: datetime | None = Query(None), date_to: datetime | None = Query(None),
                  status: str | None = Query(None), format: Fmt = Query("json"), page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=1000),
                  db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    report = svc.refund_report(db, actor, tenant_id=tenant_id, date_from=date_from, date_to=date_to, status=status, page=page, page_size=page_size)
    return _out(report, format, ["refund_id", "payment_id", "created_at", "approved_at", "completed_at", "status", "refund_type", "destination", "currency", "amount", "tax_refunded",
                                 "base_amount", "reason", "requested_by_role", "sla_due_at", "escalated", "turnaround_hours"], "refund-report")


@router.get("/settlements", summary="Settlement report", description="Every payout with its gross, refunds, commission, fees and net, and totals by currency.")
def settlement_report(tenant_id: UUID | None = Query(None, description="Super Admin only"), date_from: datetime | None = Query(None), date_to: datetime | None = Query(None),
                      status: str | None = Query(None), format: Fmt = Query("json"), page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=1000),
                      db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    report = svc.settlement_report(db, actor, tenant_id=tenant_id, date_from=date_from, date_to=date_to, status=status, page=page, page_size=page_size)
    return _out(report, format, ["settlement_id", "reference", "tenant_id", "status", "currency", "period_start", "period_end", "gross_amount", "refund_amount", "commission_amount",
                                 "fee_amount", "fixed_fee_amount", "tax_collected", "net_amount", "items_count", "paid_at", "payout_reference"], "settlement-report")


@router.get("/currency", summary="Currency report", description="Paid payments grouped by the currency customers paid in, with their value in the base currency.")
def currency_report(tenant_id: UUID | None = Query(None, description="Super Admin only"), date_from: datetime | None = Query(None), date_to: datetime | None = Query(None),
                    format: Fmt = Query("json"), db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    report = svc.currency_report(db, actor, tenant_id=tenant_id, date_from=date_from, date_to=date_to)
    return _out(report, format, ["currency", "payments", "charge_total", "tax", "refunded", "base_total", "rate_min", "rate_max"], "currency-report")


@router.get("/exchange-rates", summary="Exchange rate report (Super Admin)", description="The rate history with the low, high and average of each currency in the window.")
def exchange_rate_report(quote: str | None = Query(None, min_length=3, max_length=3), date_from: datetime | None = Query(None), date_to: datetime | None = Query(None),
                         format: Fmt = Query("json"), db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    report = svc.exchange_rate_report(db, actor, quote=quote, date_from=date_from, date_to=date_to)
    return _out(report, format, ["quote_currency", "base_currency", "rate", "source", "pinned", "created_by", "created_at", "note"], "exchange-rate-report")


@router.get("/reconciliation", summary="Reconciliation report (Super Admin)", description="The reconciliation runs, and the mismatches found by kind, open and resolved.")
def reconciliation_report(date_from: datetime | None = Query(None), date_to: datetime | None = Query(None), format: Fmt = Query("json"),
                          db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    report = svc.reconciliation_report(db, actor, date_from=date_from, date_to=date_to)
    return _out(report, format, ["run_id", "provider", "created_at", "period_start", "period_end", "rows", "matched", "mismatched"], "reconciliation-report")
