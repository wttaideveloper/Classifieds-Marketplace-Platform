"""Turns payments, refunds, invoices and settlements into the shapes the API returns."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.payment_model import Invoice, MerchantSettlement, Payment, Refund
from app.schemas.invoice_schema import InvoiceResponse
from app.schemas.payment_schema import PaymentResponse, QuoteResponse, RiskOut
from app.schemas.refund_schema import RefundResponse
from app.schemas.settlement_schema import SettlementConfigResponse, SettlementItemResponse, SettlementResponse
from app.services import currency_service, payment_service, refund_service
from app.utils.money import quantize, to_decimal


def _q(db: Session, value, code: str):
    """An amount shown with the decimals of its currency (1180.00 rupees, 2124 yen, 4.366 dinar), not the column's four."""
    return None if value is None else quantize(to_decimal(value), currency_service.decimals_for(db, code))


def payment_response(db: Session, payment: Payment, actor) -> PaymentResponse:
    manager = actor.is_super_admin or (actor.role == "merchant" and payment.tenant_id == actor.tenant_id)
    details = payment.tax_details or {}
    paid = payment.status in ("succeeded", "partially_refunded")
    return PaymentResponse(
        id=payment.id, status=payment.status, status_label=payment_service.STATUS_LABELS.get(payment.status, payment.status), purchase_type=payment.purchase_type,
        purchase_id=payment.purchase_id, attempt_no=payment.attempt_no, retry_of_id=payment.retry_of_id, tenant_id=payment.tenant_id, customer_user_id=payment.customer_user_id,
        customer_email=payment.customer_email, provider=payment.provider, capture_method=payment.capture_method, item_currency=payment.item_currency,
        subtotal_amount=_q(db, payment.subtotal_amount, payment.item_currency), tax_amount=_q(db, payment.tax_amount, payment.item_currency),
        total_amount=_q(db, payment.total_amount, payment.item_currency), charge_currency=payment.charge_currency,
        charge_subtotal=_q(db, payment.charge_subtotal, payment.charge_currency), charge_tax=_q(db, payment.charge_tax, payment.charge_currency),
        charge_total=_q(db, payment.charge_total, payment.charge_currency), exchange_rate=payment.exchange_rate,
        base_currency=payment.base_currency, base_total=_q(db, payment.base_total, payment.base_currency),
        refunded_amount=_q(db, payment.refunded_amount, payment.charge_currency),
        refundable_amount=_q(db, max(to_decimal(payment.charge_total) - to_decimal(payment.refunded_amount), Decimal(0)) if paid else Decimal(0), payment.charge_currency),
        method_brand=payment.method_brand, method_last4=payment.method_last4, failure_code=payment.failure_code, failure_message=payment.failure_message,
        next_action=payment.next_action if payment.status == "requires_action" else None, invoice_id=payment.invoice_id, lines=payment.lines or [],
        tax_breakdown=details.get("breakdown") or [], reverse_charge=bool(details.get("reverse_charge")), is_tax_exempt=bool(details.get("is_exempt")),
        billing=payment.billing, risk=RiskOut(score=payment.risk_score, decision=payment.risk_decision, reasons=payment.risk_reasons or []) if manager else None,
        needs_reconciliation=bool(payment.needs_reconciliation), expires_at=payment.expires_at, authorized_at=payment.authorized_at, captured_at=payment.captured_at,
        failed_at=payment.failed_at, cancelled_at=payment.cancelled_at, created_at=payment.created_at, updated_at=payment.updated_at,
        actions=payment_service.actions_for(actor, payment),
    )


def quote_response(db: Session, pricing, payable) -> QuoteResponse:
    calc = pricing.calc
    updated = currency_service.rate_updated_at(db, payable.currency, pricing.charge_currency)
    row = currency_service.get_currency(db, pricing.charge_currency)
    return QuoteResponse(
        purchase_type=payable.purchase_type, purchase_id=payable.purchase_id, description=payable.description, item_currency=payable.currency,
        charge_currency=pricing.charge_currency, exchange_rate=pricing.rate, rate_updated_at=updated, subtotal_amount=calc["subtotal"], tax_amount=calc["tax_total"],
        total_amount=calc["total"], charge_subtotal=pricing.subtotal, charge_tax=pricing.tax, charge_total=pricing.total, base_currency=pricing.base_currency,
        base_total=pricing.base_total, prices_include_tax=payable.prices_include_tax, is_tax_exempt=calc["is_exempt"], reverse_charge=calc["reverse_charge"],
        note=calc["note"], warnings=pricing.warnings, lines=pricing.lines, tax_breakdown=pricing.breakdown,
        min_amount=row.min_amount if row else None, max_amount=row.max_amount if row else None,
    )


def refund_response(db: Session, refund: Refund, actor) -> RefundResponse:
    return RefundResponse(
        id=refund.id, payment_id=refund.payment_id, tenant_id=refund.tenant_id, status=refund.status, status_label=refund_service.STATUS_LABELS.get(refund.status, refund.status),
        refund_type=refund.refund_type, amount=_q(db, refund.amount, refund.currency), currency=refund.currency, reason=refund.reason, destination=refund.destination, bank_reference=refund.bank_reference,
        tax_refunded=_q(db, refund.tax_refunded, refund.currency), decision_reason=refund.decision_reason, decided_at=refund.decided_at, approved_at=refund.approved_at, completed_at=refund.completed_at,
        sla_due_at=refund.sla_due_at, is_overdue=bool(refund.status in refund_service.OPEN_STATES and refund.sla_due_at and refund.sla_due_at < datetime.utcnow()),
        escalated_at=refund.escalated_at, failure_reason=refund.failure_reason, credit_note_id=refund.credit_note_id, requested_by_role=refund.requested_by_role,
        created_at=refund.created_at, actions=refund_service.actions_for(db, actor, refund),
    )


def invoice_response(invoice: Invoice, db: Session | None = None) -> InvoiceResponse:
    out = InvoiceResponse.model_validate(invoice)
    if db is not None:
        out.subtotal, out.tax_total, out.total = (_q(db, v, invoice.currency) for v in (invoice.subtotal, invoice.tax_total, invoice.total))
        out.base_total = _q(db, invoice.base_total, invoice.base_currency)
    out.download_path = f"/api/v1/invoices/{invoice.id}/download"
    return out


def settlement_response(settlement: MerchantSettlement, actor, db: Session | None = None) -> SettlementResponse:
    out = SettlementResponse.model_validate(settlement)
    if db is not None:
        for name in ("gross_amount", "refund_amount", "commission_amount", "fee_amount", "fixed_fee_amount", "tax_collected", "net_amount"):
            setattr(out, name, _q(db, getattr(settlement, name), settlement.currency))
    if actor.is_super_admin:
        if settlement.status == "pending":
            out.actions = ["mark_paid", "mark_failed", "cancel", "verify"]
        elif settlement.status == "failed":
            out.actions = ["cancel", "verify"]
        else:
            out.actions = ["verify"]
    else:
        out.actions = ["verify"]
    return out


def _pct(value):
    """A percent without trailing zeros: 10, 7.5."""
    return None if value is None else Decimal(format(to_decimal(value).normalize(), "f"))


def config_response(config, db: Session | None = None) -> SettlementConfigResponse:
    from app.core.config import settings

    out = SettlementConfigResponse.model_validate(config)
    out.commission_percent = _pct(config.commission_percent)
    out.effective_commission_percent = _pct(config.commission_percent if config.commission_percent is not None else Decimal(str(settings.PLATFORM_COMMISSION_PERCENT)))
    if db is not None:
        out.fixed_fee, out.min_payout = _q(db, config.fixed_fee, config.currency), _q(db, config.min_payout, config.currency)
    return out


def settlement_item_response(db: Session, item, settlement) -> SettlementItemResponse:
    out = SettlementItemResponse.model_validate(item)
    out.charge_amount = _q(db, item.charge_amount, item.charge_currency)
    for name in ("gross", "commission", "fee", "fixed_fee", "net"):
        setattr(out, name, _q(db, getattr(item, name), settlement.currency))
    return out
