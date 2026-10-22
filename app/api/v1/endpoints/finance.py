from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.payment_access import PayActor, require_merchant_or_admin, require_super_admin
from app.db.database import get_db
from app.models.payment_model import FinanceAuditLog, Invoice, Payment, Refund
from app.schemas.settlement_schema import (
    AuditEntryResponse, AuditListResponse, ReconcileRequest, ReconItemResponse, ReconRunListResponse, ReconRunResponse, ResolveBody,
)
from app.services import finance_audit, payment_export, payment_jobs, reconciliation_service as recon

router = APIRouter(tags=["Finance"])


@router.get("/audit", response_model=AuditListResponse, summary="Audit log (Super Admin)",
            description="Who changed a tax rule, an exchange rate, a settlement setting or decided a refund, and when. Entries are never edited or removed.")
def audit_log(
    entity_type: str | None = Query(None), entity_id: str | None = Query(None), action: str | None = Query(None), actor_id: str | None = Query(None),
    tenant_id: UUID | None = Query(None), date_from: datetime | None = Query(None), date_to: datetime | None = Query(None), page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500), db: Session = Depends(get_db), _a: PayActor = Depends(require_super_admin),
):
    query = db.query(FinanceAuditLog)
    for column, value in ((FinanceAuditLog.entity_type, entity_type), (FinanceAuditLog.entity_id, entity_id), (FinanceAuditLog.action, action),
                          (FinanceAuditLog.actor_id, actor_id), (FinanceAuditLog.tenant_id, tenant_id)):
        if value:
            query = query.filter(column == value)
    if date_from:
        query = query.filter(FinanceAuditLog.created_at >= date_from)
    if date_to:
        query = query.filter(FinanceAuditLog.created_at <= date_to)
    total = query.count()
    rows = query.order_by(FinanceAuditLog.created_at.desc(), FinanceAuditLog.id).offset((page - 1) * page_size).limit(page_size).all()
    return AuditListResponse(items=[AuditEntryResponse.model_validate(r) for r in rows], total=total, page=page, page_size=page_size)


@router.post("/reconciliation", response_model=ReconRunResponse, summary="Reconcile a gateway report (Super Admin)",
             description="FR-SET-06. Send the rows of the gateway's payment / settlement report. Each is matched to our payment or refund by the gateway id and compared on currency, amount, "
                         "status and fee; the differences are saved to be looked at. With `period_start` and `period_end`, payments of ours that the report lacks are flagged too.")
def reconcile(payload: ReconcileRequest = Body(...), db: Session = Depends(get_db), actor: PayActor = Depends(require_super_admin)):
    rows = [r.model_dump() for r in payload.rows]
    run = recon.reconcile_report(db, actor, (payload.provider or settings.PAYMENT_GATEWAY).lower(), rows, payload.period_start, payload.period_end)
    out = ReconRunResponse.model_validate(run)
    out.items = [ReconItemResponse.model_validate(i) for i in recon.run_items(db, run.id)]
    return out


@router.get("/reconciliation", response_model=ReconRunListResponse, summary="Reconciliation runs (Super Admin)")
def reconciliation_runs(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db), _a: PayActor = Depends(require_super_admin)):
    rows, total = recon.list_runs(db, page, page_size)
    return ReconRunListResponse(items=[ReconRunResponse.model_validate(r) for r in rows], total=total)


@router.get("/reconciliation/{run_id}", response_model=ReconRunResponse, summary="One reconciliation run with its mismatches (Super Admin)")
def reconciliation_run(run_id: UUID, issue: str | None = Query(None), resolved: bool | None = Query(None), db: Session = Depends(get_db), _a: PayActor = Depends(require_super_admin)):
    run = recon.get_run(db, run_id)
    out = ReconRunResponse.model_validate(run)
    out.items = [ReconItemResponse.model_validate(i) for i in recon.run_items(db, run.id, issue=issue, resolved=resolved)]
    return out


@router.post("/reconciliation/items/{item_id}/resolve", response_model=ReconItemResponse, summary="Mark a mismatch as dealt with (Super Admin)")
def resolve_item(item_id: UUID, payload: ResolveBody = Body(...), db: Session = Depends(get_db), actor: PayActor = Depends(require_super_admin)):
    return ReconItemResponse.model_validate(recon.resolve_item(db, actor, item_id, payload.note))


class JobsBody(BaseModel):
    jobs: list[Literal["rates", "payments", "refunds", "settlements", "reminders"]] = Field(default_factory=lambda: list(payment_jobs.JOBS))


@router.post("/jobs/run", summary="Run the finance jobs now (Super Admin)",
             description="Refresh exchange rates, check payments whose answer was lost, retry stuck refunds and escalate overdue ones, settle the businesses that are due, and send "
                         "retry reminders. They also run on a schedule; each is safe to run twice.")
def run_jobs(payload: JobsBody = Body(default_factory=JobsBody), db: Session = Depends(get_db), actor: PayActor = Depends(require_super_admin)):
    return payment_jobs.run_jobs(db, tuple(payload.jobs), access_token=actor.token)


@router.get("/export", summary="Export for accounting / ERP", description="Payments, refunds, invoices (with credit and debit notes) or tax lines as CSV or JSON, for the accounts team or an ERP import.")
def export(kind: Literal["payments", "refunds", "invoices", "tax"] = Query(...), format: Literal["json", "csv"] = Query("csv"), date_from: datetime | None = Query(None),
           date_to: datetime | None = Query(None), tenant_id: UUID | None = Query(None, description="Super Admin only"), db: Session = Depends(get_db),
           actor: PayActor = Depends(require_merchant_or_admin)):
    tenant = actor.tenant_id if actor.role == "merchant" else tenant_id
    if actor.role == "merchant" and tenant_id not in (None, actor.tenant_id):
        raise HTTPException(status_code=403, detail="You can only export your own business's data")
    if kind == "payments":
        query, date_col = db.query(Payment).filter(Payment.status.in_(("succeeded", "partially_refunded", "refunded"))), Payment.captured_at
        columns = ["id", "captured_at", "tenant_id", "customer_email", "purchase_type", "purchase_id", "item_currency", "total_amount", "charge_currency", "charge_subtotal", "charge_tax",
                   "charge_total", "exchange_rate", "base_currency", "base_total", "gateway_fee", "refunded_amount", "gateway_payment_id"]
        model = Payment
    elif kind == "refunds":
        query, date_col, model = db.query(Refund).filter(Refund.status == "completed"), Refund.completed_at, Refund
        columns = ["id", "payment_id", "completed_at", "tenant_id", "currency", "amount", "tax_refunded", "destination", "gateway_refund_id", "credit_note_id", "reason"]
    elif kind == "invoices":
        query, date_col, model = db.query(Invoice), Invoice.issued_at, Invoice
        columns = ["number", "doc_type", "issued_at", "tenant_id", "payment_id", "parent_invoice_id", "currency", "subtotal", "tax_total", "total", "exchange_rate", "base_currency", "base_total"]
    else:
        from app.models.payment_model import TaxTransaction

        query, date_col, model = db.query(TaxTransaction), TaxTransaction.created_at, TaxTransaction
        columns = ["id", "created_at", "payment_id", "refund_id", "tenant_id", "tax_type", "name", "country", "state", "rate", "taxable_amount", "tax_amount", "item_currency",
                   "tax_amount_charge", "charge_currency", "is_exempt", "is_reverse_charge"]
    if tenant:
        query = query.filter(model.tenant_id == tenant)
    if date_from:
        query = query.filter(date_col >= date_from)
    if date_to:
        query = query.filter(date_col <= date_to)
    rows = [{c: getattr(r, c) for c in columns} for r in query.order_by(date_col).limit(50000).all()]
    if format == "csv":
        return payment_export.csv_response(rows, columns, f"{kind}-export.csv")
    return finance_audit.jsonable({"kind": kind, "count": len(rows), "rows": rows})
