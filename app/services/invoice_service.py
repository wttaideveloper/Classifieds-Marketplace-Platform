"""Invoices, credit notes and debit notes.

An invoice is issued the moment a payment succeeds, in the currency the customer paid in, with the tax broken down by component,
the exchange rate used, and the value in the base currency. Numbers run in an unbroken series per business and year
(INV-2026-000001), so a gap always means something is missing. A refund issues a credit note against the invoice; a correction
upward is a debit note. Nothing is ever edited after issue: a mistake is corrected by a credit or debit note.
"""
import re
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enterprise_model import Enterprise
from app.models.payment_model import Invoice, InvoiceSequence, InvoiceTemplate, MerchantTaxProfile, Payment
from app.services import currency_service, finance_audit, invoice_pdf, payment_notifications
from app.utils.money import quantize, to_decimal

DOC_PREFIX = {"credit_note": "CN", "debit_note": "DN"}
DEFAULT_TEMPLATE = {"display_name": None, "logo_url": None, "address": None, "footer_text": None, "terms": None, "number_prefix": "INV",
                    "locale": "en-IN", "accent_color": "#1f3a5f", "show_exchange_rate": True}
TEMPLATE_FIELDS = ("display_name", "logo_url", "address", "footer_text", "terms", "number_prefix", "locale", "accent_color", "show_exchange_rate")


def _now() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------- numbering

def next_number(db: Session, tenant_id: UUID | None, prefix: str, year: int) -> tuple[str, str]:
    """(series key, number). The sequence row is locked while the number is taken, so two payments never get the same one."""
    series = f"{prefix}:{tenant_id or 'platform'}:{year}"
    row = db.query(InvoiceSequence).filter(InvoiceSequence.series_key == series).with_for_update().first()
    if row is None:
        try:
            with db.begin_nested():
                db.add(InvoiceSequence(series_key=series, last_number=0))
        except IntegrityError:
            pass
        row = db.query(InvoiceSequence).filter(InvoiceSequence.series_key == series).with_for_update().first()
    row.last_number += 1
    db.flush()
    return series, f"{prefix}-{year}-{row.last_number:06d}"


# ---------------------------------------------------------------- templates

def template_for(db: Session, tenant_id: UUID | None) -> dict:
    """The business's own template, else the platform default, else built-in wording."""
    for key in (tenant_id, None):
        row = db.query(InvoiceTemplate).filter(InvoiceTemplate.tenant_id == key).first() if key is not None else \
            db.query(InvoiceTemplate).filter(InvoiceTemplate.tenant_id.is_(None)).first()
        if row is not None:
            return {**{f: getattr(row, f) for f in TEMPLATE_FIELDS}, "id": row.id, "tenant_id": row.tenant_id}
    return {**DEFAULT_TEMPLATE, "id": None, "tenant_id": None}


def save_template(db: Session, actor, tenant_id: UUID | None, payload) -> InvoiceTemplate:
    row = db.query(InvoiceTemplate).filter(InvoiceTemplate.tenant_id == tenant_id).first() if tenant_id else \
        db.query(InvoiceTemplate).filter(InvoiceTemplate.tenant_id.is_(None)).first()
    before = finance_audit.snapshot(row, *TEMPLATE_FIELDS) if row else None
    if row is None:
        row = InvoiceTemplate(tenant_id=tenant_id)
        db.add(row)
    for field_name, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field_name, value)
    row.updated_by = actor.id
    db.flush()
    finance_audit.record(db, actor, "invoice_template_saved", "invoice_template", row.id, before=before, after=finance_audit.snapshot(row, *TEMPLATE_FIELDS), tenant_id=tenant_id)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------- building

def _supplier(db: Session, tenant_id: UUID | None) -> dict:
    out = {"name": None, "address": None, "tax_id": None, "tax_id_label": "Tax ID", "country": None, "state": None}
    if tenant_id is None:
        return out
    enterprise = db.query(Enterprise).filter(Enterprise.tenant_id == tenant_id, Enterprise.is_deleted.is_(False)).order_by(Enterprise.created_at).first()
    if enterprise is not None:
        out["name"] = enterprise.business_legal_name or enterprise.business_short_name
        out["address"] = enterprise.registered_address or enterprise.business_address
    profile = db.get(MerchantTaxProfile, tenant_id)
    if profile is not None:
        out.update({"tax_id": profile.tax_id, "country": profile.country, "state": profile.state,
                    "tax_id_label": "GSTIN" if profile.tax_id_type == "gstin" else "VAT No." if profile.tax_id_type == "vat" else "Tax ID"})
        out["name"] = profile.legal_name or out["name"]
        out["address"] = profile.address or out["address"]
    return out


def _notes(payment: Payment) -> str | None:
    details = payment.tax_details or {}
    notes = []
    if details.get("note"):
        notes.append(details["note"])
    if details.get("is_exempt"):
        notes.append("Tax exempt: " + str(((details.get("calc") or {}).get("exempt_reason")) or "exemption certificate"))
    return ". ".join(notes) or None


def issue_for_payment(db: Session, payment: Payment) -> Invoice:
    """The invoice of a paid payment. Called once, inside the transaction that marks the payment paid."""
    existing = db.query(Invoice).filter(Invoice.payment_id == payment.id, Invoice.doc_type == "invoice").first()
    if existing is not None:
        payment.invoice_id = existing.id
        return existing
    template = template_for(db, payment.tenant_id)
    year = _now().year
    series, number = next_number(db, payment.tenant_id, template["number_prefix"] or "INV", year)
    rate = to_decimal(payment.exchange_rate)
    lines = []
    for line in payment.lines or []:
        qty = int(line["quantity"])
        net = to_decimal(line["charge_net_amount"])
        lines.append({
            "description": line["description"], "category": line.get("category"), "quantity": qty,
            "unit_price": format(quantize(net / qty, currency_service.decimals_for(db, payment.charge_currency)), "f"), "net": format(net, "f"),
            "tax": format(to_decimal(line["charge_tax_amount"]), "f"), "gross": format(to_decimal(line["charge_gross_amount"]), "f"),
            "taxes": [{"name": t["name"], "rate": str(t["rate"]), "amount": str(t.get("tax_amount_charge", "0")), "reverse_charge": t.get("reverse_charge", False)}
                      for t in line.get("taxes", [])],
        })
    invoice = Invoice(
        doc_type="invoice", number=number, series_key=series, tenant_id=payment.tenant_id, customer_user_id=payment.customer_user_id, payment_id=payment.id,
        status="issued", currency=payment.charge_currency, item_currency=payment.item_currency, base_currency=payment.base_currency, exchange_rate=rate,
        base_rate=payment.base_rate, subtotal=payment.charge_subtotal, tax_total=payment.charge_tax, total=payment.charge_total, base_total=payment.base_total,
        lines=lines, tax_breakdown=(payment.tax_details or {}).get("breakdown", []), billing=payment.billing, supplier=_supplier(db, payment.tenant_id),
        notes=_notes(payment), template_id=template["id"], issued_at=_now(), created_by="system",
    )
    db.add(invoice)
    db.flush()
    payment.invoice_id = invoice.id
    return invoice


def issue_credit_note(db: Session, payment: Payment, refund) -> Invoice:
    """A credit note for a refund: the refunded share of the invoice, tax included, in the same currency."""
    parent = db.get(Invoice, payment.invoice_id) if payment.invoice_id else None
    if parent is None:
        parent = issue_for_payment(db, payment)
    decimals = currency_service.decimals_for(db, payment.charge_currency)
    amount = to_decimal(refund.amount)
    share = amount / to_decimal(payment.charge_total)
    tax_part = min(quantize(to_decimal(payment.charge_tax) * share, decimals), amount)
    subtotal = amount - tax_part
    breakdown, left = [], tax_part
    rows = list(parent.tax_breakdown or [])
    for position, row in enumerate(rows):
        portion = left if position == len(rows) - 1 else min(left, quantize(to_decimal(row["tax_amount"]) * share, decimals))
        left -= portion
        breakdown.append({**row, "tax_amount": format(portion, "f"), "taxable_amount": format(quantize(to_decimal(row["taxable_amount"]) * share, decimals), "f")})
    series, number = next_number(db, payment.tenant_id, DOC_PREFIX["credit_note"], _now().year)
    note = Invoice(
        doc_type="credit_note", number=number, series_key=series, tenant_id=payment.tenant_id, customer_user_id=payment.customer_user_id, payment_id=payment.id,
        refund_id=refund.id, parent_invoice_id=parent.id, status="issued", currency=payment.charge_currency, item_currency=payment.item_currency,
        base_currency=payment.base_currency, exchange_rate=payment.exchange_rate, base_rate=payment.base_rate, subtotal=subtotal, tax_total=tax_part, total=amount,
        base_total=quantize(amount * to_decimal(payment.base_rate), currency_service.decimals_for(db, payment.base_currency)),
        lines=[{"description": f"Refund against invoice {parent.number}" + (f": {refund.reason}" if refund.reason else ""), "category": "refund", "quantity": 1,
                "unit_price": format(subtotal, "f"), "net": format(subtotal, "f"), "tax": format(tax_part, "f"), "gross": format(amount, "f"), "taxes": []}],
        tax_breakdown=breakdown, billing=parent.billing, supplier=parent.supplier, notes=f"Credit note for {'full' if refund.refund_type == 'full' else 'partial'} refund",
        reason=refund.reason, template_id=parent.template_id, issued_at=_now(), created_by="system",
    )
    db.add(note)
    db.flush()
    return note


def create_debit_note(db: Session, actor, invoice_id: UUID, subtotal: Decimal, tax_amount: Decimal, reason: str) -> Invoice:
    parent = get_invoice(db, actor, invoice_id)
    if parent.doc_type != "invoice":
        raise HTTPException(status_code=409, detail="A debit note is raised against an invoice")
    if actor.role == "customer" or (actor.role == "merchant" and parent.tenant_id != actor.tenant_id):
        raise HTTPException(status_code=403, detail="Only the business or the platform can raise a debit note")
    decimals = currency_service.decimals_for(db, parent.currency)
    subtotal, tax_amount = quantize(to_decimal(subtotal), decimals), quantize(to_decimal(tax_amount), decimals)
    total = subtotal + tax_amount
    if total <= 0:
        raise HTTPException(status_code=422, detail="A debit note must be for more than zero")
    series, number = next_number(db, parent.tenant_id, DOC_PREFIX["debit_note"], _now().year)
    note = Invoice(
        doc_type="debit_note", number=number, series_key=series, tenant_id=parent.tenant_id, customer_user_id=parent.customer_user_id, payment_id=parent.payment_id,
        parent_invoice_id=parent.id, status="issued", currency=parent.currency, item_currency=parent.item_currency, base_currency=parent.base_currency,
        exchange_rate=parent.exchange_rate, base_rate=parent.base_rate, subtotal=subtotal, tax_total=tax_amount, total=total,
        base_total=quantize(total * to_decimal(parent.base_rate), currency_service.decimals_for(db, parent.base_currency)),
        lines=[{"description": f"Adjustment to invoice {parent.number}: {reason}", "category": "adjustment", "quantity": 1, "unit_price": format(subtotal, "f"),
                "net": format(subtotal, "f"), "tax": format(tax_amount, "f"), "gross": format(total, "f"), "taxes": []}],
        tax_breakdown=[], billing=parent.billing, supplier=parent.supplier, reason=reason, template_id=parent.template_id, issued_at=_now(), created_by=actor.id,
    )
    db.add(note)
    db.flush()
    finance_audit.record(db, actor, "debit_note_issued", "invoice", note.id, after={"number": number, "total": total, "parent": parent.number}, tenant_id=parent.tenant_id, reason=reason)
    db.commit()
    db.refresh(note)
    payment_notifications.notify_invoice(note.id)
    return note


# ---------------------------------------------------------------- reading

def _can_view(actor, invoice: Invoice) -> bool:
    return (actor.is_super_admin or (invoice.customer_user_id is not None and str(invoice.customer_user_id) == actor.id)
            or (actor.role == "merchant" and invoice.tenant_id is not None and invoice.tenant_id == actor.tenant_id))


def get_invoice(db: Session, actor, invoice_id: UUID) -> Invoice:
    invoice = db.get(Invoice, invoice_id)
    if invoice is None or not _can_view(actor, invoice):
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


def list_invoices(db: Session, actor, *, doc_type: str | None = None, payment_id: UUID | None = None, search: str | None = None, tenant_id: UUID | None = None,
                  date_from: datetime | None = None, date_to: datetime | None = None, page: int = 1, page_size: int = 20) -> tuple[list[Invoice], int]:
    query = db.query(Invoice)
    if actor.role == "customer":
        query = query.filter(Invoice.customer_user_id == actor.user_uuid)
    elif actor.role == "merchant":
        query = query.filter(or_(Invoice.tenant_id == actor.tenant_id, Invoice.customer_user_id == actor.user_uuid))
    elif tenant_id:
        query = query.filter(Invoice.tenant_id == tenant_id)
    if doc_type:
        query = query.filter(Invoice.doc_type == doc_type)
    if payment_id:
        query = query.filter(Invoice.payment_id == payment_id)
    if search and search.strip():
        query = query.filter(Invoice.number.ilike(f"%{search.strip()}%"))
    if date_from:
        query = query.filter(Invoice.issued_at >= date_from)
    if date_to:
        query = query.filter(Invoice.issued_at <= date_to)
    total = query.count()
    return query.order_by(Invoice.issued_at.desc(), Invoice.number.desc()).offset((page - 1) * page_size).limit(page_size).all(), total


def pdf_bytes(db: Session, invoice: Invoice) -> bytes:
    template = template_for(db, invoice.tenant_id)
    if invoice.template_id and not template.get("id"):
        template = template_for(db, None)
    parent = db.get(Invoice, invoice.parent_invoice_id) if invoice.parent_invoice_id else None
    context = {
        "doc_type": invoice.doc_type, "number": invoice.number, "issued_at": invoice.issued_at, "currency": invoice.currency, "item_currency": invoice.item_currency,
        "base_currency": invoice.base_currency, "exchange_rate": float(invoice.exchange_rate), "base_rate": float(invoice.base_rate),
        "subtotal": invoice.subtotal, "tax_total": invoice.tax_total, "total": invoice.total, "base_total": invoice.base_total, "lines": invoice.lines or [],
        "tax_breakdown": invoice.tax_breakdown or [], "billing": invoice.billing or {}, "supplier": invoice.supplier or {}, "notes": invoice.notes,
        "reason": invoice.reason if invoice.doc_type != "credit_note" else None, "parent_number": parent.number if parent else None,
    }
    return invoice_pdf.render(context, template, currency_service.decimals_for(db, invoice.currency), currency_service.decimals_for(db, invoice.base_currency))


def mark_emailed(db: Session, actor, invoice_id: UUID) -> Invoice:
    invoice = get_invoice(db, actor, invoice_id)
    invoice.emailed_at, invoice.email_count = _now(), (invoice.email_count or 0) + 1
    db.commit()
    db.refresh(invoice)
    payment_notifications.notify_invoice(invoice.id, email=True, access_token=actor.token)
    return invoice


_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
