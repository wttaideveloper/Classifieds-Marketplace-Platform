from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.payment_access import PayActor, get_pay_actor, require_merchant_or_admin
from app.db.database import get_db
from app.schemas.invoice_schema import DebitNoteCreate, InvoiceListResponse, InvoiceResponse, TemplateBody, TemplateResponse
from app.services import invoice_service as svc
from app.services import payment_service, payment_view

router = APIRouter(tags=["Invoices"])


class GenerateBody(BaseModel):
    payment_id: UUID


def _template(row: dict, tenant_id: UUID | None) -> TemplateResponse:
    return TemplateResponse(tenant_id=row.get("tenant_id"), is_default=tenant_id is not None and row.get("tenant_id") is None,
                            **{k: row[k] for k in svc.TEMPLATE_FIELDS})


@router.get("", response_model=InvoiceListResponse, summary="Invoice history",
            description="FR-INV-04. Invoices, credit notes and debit notes: a customer sees their own, a business its own, the platform all. Search by number.")
def invoice_history(
    doc_type: str | None = Query(None, pattern="^(invoice|credit_note|debit_note)$"), payment_id: UUID | None = Query(None), search: str | None = Query(None),
    tenant_id: UUID | None = Query(None, description="Super Admin only"), date_from: datetime | None = Query(None), date_to: datetime | None = Query(None),
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200), db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor),
):
    rows, total = svc.list_invoices(db, actor, doc_type=doc_type, payment_id=payment_id, search=search, tenant_id=tenant_id, date_from=date_from, date_to=date_to,
                                    page=page, page_size=page_size)
    return InvoiceListResponse(items=[payment_view.invoice_response(r, db) for r in rows], total=total, page=page, page_size=page_size)


@router.post("/generate", response_model=InvoiceResponse, summary="Generate the invoice of a paid payment",
             description="FR-INV-01. An invoice is issued automatically when a payment succeeds; this returns it, or creates it for a paid payment that has none. "
                         "Calling it again returns the same invoice (the number never changes).")
def generate_invoice(payload: GenerateBody = Body(...), db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    payment = payment_service.get_visible(db, actor, payload.payment_id)
    if payment.status not in ("succeeded", "partially_refunded", "refunded"):
        raise HTTPException(status_code=409, detail="An invoice is issued once the payment is paid")
    invoice = svc.issue_for_payment(db, payment)
    db.commit()
    db.refresh(invoice)
    return payment_view.invoice_response(invoice, db)


@router.get("/templates", response_model=TemplateResponse, summary="Invoice template (business / platform)",
            description="FR-INV-09. The wording and look of a business's invoices. A business with none of its own gets the platform default (`is_default`).")
def get_template(tenant_id: UUID | None = Query(None, description="Super Admin: the business. Leave out for the platform default"), db: Session = Depends(get_db),
                 actor: PayActor = Depends(require_merchant_or_admin)):
    tenant = actor.tenant_id if actor.role == "merchant" else tenant_id
    return _template(svc.template_for(db, tenant), tenant)


@router.put("/templates", response_model=TemplateResponse, summary="Save an invoice template",
            description="Changes apply to invoices issued from now on; an issued invoice is never altered. A business saves its own; the Super Admin saves a business's or, "
                        "without tenant_id, the platform default.")
def save_template(payload: TemplateBody = Body(...), tenant_id: UUID | None = Query(None, description="Super Admin only"), db: Session = Depends(get_db),
                  actor: PayActor = Depends(require_merchant_or_admin)):
    if actor.role == "merchant" and tenant_id not in (None, actor.tenant_id):
        raise HTTPException(status_code=403, detail="You can only change your own business's template")
    tenant = actor.tenant_id if actor.role == "merchant" else tenant_id
    svc.save_template(db, actor, tenant, payload)
    return _template(svc.template_for(db, tenant), tenant)


@router.get("/{invoice_id}", response_model=InvoiceResponse, summary="Invoice details")
def get_invoice(invoice_id: UUID = Path(...), db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    return payment_view.invoice_response(svc.get_invoice(db, actor, invoice_id), db)


@router.get("/{invoice_id}/download", summary="Download the invoice as a PDF",
            description="FR-INV-02. Amounts are in the currency the customer paid in, with the tax broken down and the exchange rate used.",
            responses={200: {"content": {"application/pdf": {}}}})
def download_invoice(invoice_id: UUID, db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    invoice = svc.get_invoice(db, actor, invoice_id)
    return Response(content=svc.pdf_bytes(db, invoice), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{invoice.number}.pdf"'})


@router.post("/{invoice_id}/email", response_model=InvoiceResponse, summary="Send the invoice to the customer by e-mail",
             description="FR-INV-03. Sent to the customer's own account address through the notification service. A business or the platform can resend it.")
def email_invoice(invoice_id: UUID, db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    return payment_view.invoice_response(svc.mark_emailed(db, actor, invoice_id), db)


@router.post("/{invoice_id}/debit-notes", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED, summary="Raise a debit note (business / platform)",
             description="FR-INV-08. A correction upward against an invoice, with a reason. A refund raises the credit note by itself.")
def raise_debit_note(invoice_id: UUID, payload: DebitNoteCreate = Body(...), db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    return payment_view.invoice_response(svc.create_debit_note(db, actor, invoice_id, payload.subtotal, payload.tax_amount, payload.reason), db)
