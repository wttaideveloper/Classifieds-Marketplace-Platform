"""Refunds: full and partial, requested by the customer or raised by the business / platform, with a target turnaround.

    requested   a customer asked; the business (or the platform) decides                    -> approve / reject
    approved    accepted; being sent to the gateway (or waiting for a bank transfer)
    processing  with the gateway, which has not confirmed yet (a webhook completes it)
    completed   the money is on its way back; the credit note exists; the tax is reversed; the order is updated
    failed      the gateway refused it (the reason is kept)                                  -> retry
    rejected    declined, with a reason the customer can read

Where the money goes (FR-REF-06): `original` returns it to the payment method it came from through the gateway (possible for 180
days). `bank_transfer` is paid by finance outside the gateway and marked done with a reference. `wallet` is not enabled until a
wallet exists. A refund that is still open when its target time passes is escalated to the Super Admins (FR-REF-07).
"""
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.payment_model import Payment, Refund
from app.services import currency_service, finance_audit, invoice_service, payment_notifications, payment_payables, tax_service
from app.services.payment_gateways import base as gw
from app.services.payment_gateways import get_gateway
from app.utils.money import quantize, to_decimal, to_minor

logger = logging.getLogger(__name__)

GATEWAY_REFUND_DAYS = 180
OPEN_STATES = ("requested", "approved", "processing")
STATUS_LABELS = {"requested": "Requested", "approved": "Approved", "processing": "Processing", "completed": "Completed", "failed": "Failed", "rejected": "Rejected"}


def _now() -> datetime:
    return datetime.utcnow()


def _err(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


def _manages(actor, tenant_id) -> bool:
    return actor.is_super_admin or (actor.role == "merchant" and tenant_id is not None and tenant_id == actor.tenant_id)


def refundable(db: Session, payment: Payment) -> Decimal:
    """What can still be asked back: the payment less what was refunded and what is already being refunded."""
    reserved = sum((to_decimal(r.amount) for r in db.query(Refund).filter(Refund.payment_id == payment.id, Refund.status.in_(OPEN_STATES)).all()), Decimal(0))
    return to_decimal(payment.charge_total) - to_decimal(payment.refunded_amount) - reserved


# ---------------------------------------------------------------- asking

def request_refund(db: Session, actor, body, *, access_token: str | None = None) -> Refund:
    from app.services import payment_service

    payment = payment_service.get_visible(db, actor, body.payment_id)
    payer = payment_service.is_payer(actor, payment)
    manager = _manages(actor, payment.tenant_id)
    if not (payer or manager):
        raise _err(404, "Payment not found")
    if payment.status not in ("succeeded", "partially_refunded"):
        raise _err(409, "Only a paid payment can be refunded" if payment.status != "refunded" else "This payment is already fully refunded")
    decimals = currency_service.decimals_for(db, payment.charge_currency)
    left = refundable(db, payment)
    if left <= 0:
        raise _err(409, "There is nothing left to refund on this payment, or a refund is already in progress")
    amount = quantize(to_decimal(body.amount), decimals) if body.amount is not None else left
    if amount <= 0:
        raise _err(422, "The refund must be more than zero")
    if amount > left:
        raise _err(422, f"The most that can still be refunded is {payment.charge_currency} {left:f}")
    reason = " ".join((body.reason or "").split()) or None
    if not manager and (reason is None or len(reason) < 3):
        raise _err(422, "Tell us why you want a refund (at least 3 characters)")

    destination = body.destination or "original"
    if not manager and destination != "original":
        raise _err(403, "Only the business or the platform can choose where the refund goes")
    if destination == "wallet":
        raise _err(422, "Wallet refunds are not available")
    if destination == "original" and (payment.captured_at or payment.created_at) < _now() - timedelta(days=GATEWAY_REFUND_DAYS):
        raise _err(409, f"This payment is more than {GATEWAY_REFUND_DAYS} days old, so it can no longer be refunded to the original method. Use bank_transfer")

    refund = Refund(
        payment_id=payment.id, tenant_id=payment.tenant_id, requested_by=actor.user_uuid, requested_by_role=actor.role, amount=amount, currency=payment.charge_currency,
        refund_type="full" if amount == to_decimal(payment.charge_total) else "partial", reason=reason, status="requested", destination=destination,
        bank_reference=body.bank_reference, sla_due_at=_now() + timedelta(hours=settings.REFUND_SLA_HOURS),
    )
    db.add(refund)
    db.flush()
    if manager:
        _approve_and_process(db, refund, payment, actor, note=None, access_token=access_token)
    else:
        db.commit()
        payment_notifications.notify_refund(refund.id, "requested", access_token=access_token)
    db.refresh(refund)
    return refund


def decide_refund(db: Session, actor, refund_id: UUID, action: str, reason: str | None, *, access_token: str | None = None) -> Refund:
    refund = get_refund(db, actor, refund_id)
    if not _manages(actor, refund.tenant_id):
        raise _err(403, "Only the business or the platform can decide a refund")
    if refund.status != "requested":
        raise _err(409, f"This refund is {STATUS_LABELS[refund.status].lower()}")
    payment = db.get(Payment, refund.payment_id)
    note = " ".join((reason or "").split()) or None
    if action == "reject":
        if note is None or len(note) < 3:
            raise _err(422, "Give the customer a reason (at least 3 characters)")
        refund.status, refund.decided_by, refund.decided_at, refund.decision_reason = "rejected", actor.id, _now(), note[:500]
        finance_audit.record(db, actor, "refund_rejected", "refund", refund.id, after={"status": "rejected", "amount": refund.amount}, tenant_id=refund.tenant_id, reason=note)
        db.commit()
        payment_notifications.notify_refund(refund.id, "rejected", access_token=access_token)
    else:
        _approve_and_process(db, refund, payment, actor, note=note, access_token=access_token)
    db.refresh(refund)
    return refund


def _approve_and_process(db: Session, refund: Refund, payment: Payment, actor, *, note: str | None, access_token: str | None) -> None:
    refund.status, refund.approved_at, refund.decided_by, refund.decided_at, refund.decision_reason = "approved", _now(), actor.id, _now(), note
    refund.sla_due_at = _now() + timedelta(hours=settings.REFUND_SLA_HOURS)
    finance_audit.record(db, actor, "refund_approved", "refund", refund.id, after={"status": "approved", "amount": refund.amount, "destination": refund.destination},
                         tenant_id=refund.tenant_id, reason=note)
    db.commit()
    payment_notifications.notify_refund(refund.id, "approved", access_token=access_token)
    process(db, refund, access_token=access_token)


# ---------------------------------------------------------------- processing

def process(db: Session, refund: Refund, *, access_token: str | None = None) -> Refund:
    """Sends an approved refund to the gateway. Safe to call again: the gateway is given the same idempotency key every time."""
    payment = db.get(Payment, refund.payment_id)
    if refund.status not in ("approved", "processing", "failed"):
        return refund
    if refund.destination != "original":
        refund.status = "processing"           # finance pays it and then marks it done
        db.commit()
        return refund
    from app.services import payment_service

    request = payment_service._gateway_request(db, payment)
    try:
        result = get_gateway(payment.provider).refund(request, to_minor(to_decimal(refund.amount), currency_service.decimals_for(db, refund.currency)),
                                                      refund.reason, f"refund-{refund.id}")
    except gw.GatewayError as exc:
        refund.status, refund.failure_reason = ("processing", None) if exc.retryable else ("failed", exc.message[:255])
        db.commit()
        if refund.status == "failed":
            logger.warning("refund %s failed: %s", refund.id, exc.message)
        return refund
    refund.gateway_refund_id = result.gateway_refund_id or refund.gateway_refund_id
    if result.status == "succeeded":
        complete(db, refund, payment, access_token=access_token)
    elif result.status == "pending":
        refund.status = "processing"
        db.commit()
    else:
        refund.status, refund.failure_reason = "failed", (result.failure_message or "The gateway refused the refund")[:255]
        db.commit()
    db.refresh(refund)
    return refund


def complete(db: Session, refund: Refund, payment: Payment, *, access_token: str | None = None) -> None:
    """The money is on its way back: records it on the payment, reverses the tax, issues the credit note, updates the order."""
    if refund.status == "completed":
        return
    decimals = currency_service.decimals_for(db, payment.charge_currency)
    refund.status, refund.completed_at, refund.failure_reason = "completed", _now(), None
    payment.refunded_amount = to_decimal(payment.refunded_amount) + to_decimal(refund.amount)
    fully = payment.refunded_amount >= to_decimal(payment.charge_total)
    payment.status = "refunded" if fully else "partially_refunded"
    share = to_decimal(refund.amount) / to_decimal(payment.charge_total)
    refund.tax_refunded = quantize(to_decimal(payment.charge_tax) * share, decimals)
    calc = (payment.tax_details or {}).get("calc") or {}
    tax_service.record_transactions(db, payment, calc, charge_per_item=to_decimal(payment.exchange_rate), sign=-1, refund_id=refund.id, share=share)
    note = invoice_service.issue_credit_note(db, payment, refund)
    refund.credit_note_id = note.id
    payment_payables.on_refunded(db, payment, fully)
    finance_audit.record(db, None, "refund_completed", "refund", refund.id, after={"amount": refund.amount, "payment": payment.id, "credit_note": note.number},
                         tenant_id=refund.tenant_id)
    db.commit()
    payment_notifications.notify_refund(refund.id, "completed", access_token=access_token)
    payment_notifications.notify_invoice(note.id)


def retry_refund(db: Session, actor, refund_id: UUID, *, access_token: str | None = None) -> Refund:
    refund = get_refund(db, actor, refund_id)
    if not _manages(actor, refund.tenant_id):
        raise _err(403, "Only the business or the platform can retry a refund")
    if refund.status not in ("failed", "processing"):
        raise _err(409, f"A refund that is {STATUS_LABELS[refund.status].lower()} cannot be retried")
    return process(db, refund, access_token=access_token)


def complete_manual(db: Session, actor, refund_id: UUID, bank_reference: str, *, access_token: str | None = None) -> Refund:
    """Finance paid a bank-transfer refund outside the gateway and records the reference."""
    refund = get_refund(db, actor, refund_id)
    if not actor.is_super_admin:
        raise _err(403, "Only the platform's finance team can complete a bank-transfer refund")
    if refund.destination != "bank_transfer" or refund.status not in ("approved", "processing"):
        raise _err(409, "This refund is not waiting for a bank transfer")
    refund.bank_reference = bank_reference.strip()[:120]
    complete(db, refund, db.get(Payment, refund.payment_id), access_token=access_token)
    db.refresh(refund)
    return refund


def refund_system(db: Session, payment: Payment, reason: str) -> Refund:
    """An automatic full refund of what is left (money received by mistake, a duplicate payment). Needs no approval."""
    left = refundable(db, payment)
    if left <= 0:
        raise _err(409, "There is nothing left to refund")
    refund = Refund(payment_id=payment.id, tenant_id=payment.tenant_id, requested_by=None, requested_by_role="system", amount=left, currency=payment.charge_currency,
                    refund_type="full" if left == to_decimal(payment.charge_total) else "partial", reason=reason[:500], status="approved", destination="original",
                    approved_at=_now(), decided_at=_now(), decided_by="system", sla_due_at=_now() + timedelta(hours=settings.REFUND_SLA_HOURS))
    db.add(refund)
    db.flush()
    db.commit()
    payment_notifications.notify_refund(refund.id, "approved")
    return process(db, refund)


# ---------------------------------------------------------------- the gateway tells us

def apply_gateway_refund_event(db: Session, event: gw.WebhookEvent) -> Payment | None:
    """A refund webhook: completes (or fails) the matching open refund. Returns the payment, or None when nothing matched."""
    refund = None
    if event.gateway_refund_id:
        refund = db.query(Refund).filter(Refund.gateway_refund_id == event.gateway_refund_id).first()
    if refund is None:
        payment = db.query(Payment).filter(or_(Payment.gateway_order_id == event.gateway_order_id, Payment.gateway_payment_id == event.gateway_payment_id)).first() \
            if (event.gateway_order_id or event.gateway_payment_id) else None
        if payment is not None:
            refund = db.query(Refund).filter(Refund.payment_id == payment.id, Refund.status == "processing", Refund.destination == "original").order_by(Refund.created_at).first()
    if refund is None:
        return None
    payment = db.get(Payment, refund.payment_id)
    if event.type == "refund.succeeded":
        refund.gateway_refund_id = refund.gateway_refund_id or event.gateway_refund_id
        complete(db, refund, payment)
    elif refund.status in OPEN_STATES:
        refund.status, refund.failure_reason = "failed", (event.failure_message or "The gateway could not complete the refund")[:255]
    return payment


# ---------------------------------------------------------------- reading

def get_refund(db: Session, actor, refund_id: UUID) -> Refund:
    refund = db.get(Refund, refund_id)
    if refund is None:
        raise _err(404, "Refund not found")
    payment = db.get(Payment, refund.payment_id)
    from app.services import payment_service

    if not (actor.is_super_admin or _manages(actor, refund.tenant_id) or (payment is not None and payment_service.is_payer(actor, payment))):
        raise _err(404, "Refund not found")
    return refund


def list_refunds(db: Session, actor, *, status: str | None = None, payment_id: UUID | None = None, tenant_id: UUID | None = None, overdue: bool | None = None,
                 date_from: datetime | None = None, date_to: datetime | None = None, page: int = 1, page_size: int = 20) -> tuple[list[Refund], int]:
    query = db.query(Refund).join(Payment, Payment.id == Refund.payment_id)
    if actor.role == "customer":
        query = query.filter(Payment.customer_user_id == actor.user_uuid)
    elif actor.role == "merchant":
        query = query.filter(or_(Refund.tenant_id == actor.tenant_id, Payment.customer_user_id == actor.user_uuid))
    elif tenant_id:
        query = query.filter(Refund.tenant_id == tenant_id)
    if status:
        query = query.filter(Refund.status == status)
    if payment_id:
        query = query.filter(Refund.payment_id == payment_id)
    if overdue:
        query = query.filter(Refund.status.in_(OPEN_STATES), Refund.sla_due_at < _now())
    if date_from:
        query = query.filter(Refund.created_at >= date_from)
    if date_to:
        query = query.filter(Refund.created_at <= date_to)
    total = query.count()
    return query.order_by(Refund.created_at.desc(), Refund.id).offset((page - 1) * page_size).limit(page_size).all(), total


def actions_for(db: Session, actor, refund: Refund) -> list[str]:
    out = []
    if _manages(actor, refund.tenant_id):
        if refund.status == "requested":
            out += ["approve", "reject"]
        if refund.status in ("failed", "processing") and refund.destination == "original":
            out.append("retry")
        if actor.is_super_admin and refund.destination == "bank_transfer" and refund.status in ("approved", "processing"):
            out.append("complete_manual")
    if refund.credit_note_id:
        out.append("download_credit_note")
    return out


# ---------------------------------------------------------------- SLA

def escalate_overdue(db: Session, now: datetime | None = None, *, access_token: str | None = None) -> int:
    """Tells the Super Admins about each refund that is still open past its target time. Once per refund."""
    now = now or _now()
    late = db.query(Refund).filter(Refund.status.in_(OPEN_STATES), Refund.sla_due_at < now, Refund.escalated_at.is_(None)).limit(500).all()
    for refund in late:
        refund.escalated_at = now
        finance_audit.record(db, None, "refund_escalated", "refund", refund.id, after={"sla_due_at": refund.sla_due_at, "status": refund.status}, tenant_id=refund.tenant_id)
    db.commit()
    for refund in late:
        payment_notifications.notify_refund(refund.id, "escalated", access_token=access_token)
    return len(late)


def retry_stuck(db: Session, now: datetime | None = None) -> int:
    """Re-sends refunds left 'processing' at the gateway (a timeout). The idempotency key makes this safe."""
    now = now or _now()
    stuck = db.query(Refund).filter(Refund.status == "processing", Refund.destination == "original", Refund.updated_at < now - timedelta(minutes=15)).limit(100).all()
    for refund in stuck:
        process(db, refund)
    return len(stuck)
