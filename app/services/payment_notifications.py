"""Payment, refund, invoice, settlement and tax-rule notifications, on the existing platform feed and push.

    payment_succeeded         -> the payer                      the payment went through
    payment_received          -> the merchant's admins          a customer paid
    payment_failed            -> the payer                      with how to try again
    payment_retry_reminder    -> the payer                      a failed payment was not retried
    payment_review_required   -> the Super Admins               a payment is held for a person (risk or gateway timeout)
    invoice_generated         -> the payer                      (and the invoice by e-mail, when asked)
    refund_requested          -> the merchant's admins          a customer asked for a refund
    refund_approved / refund_rejected / refund_completed -> the payer
    refund_escalated          -> the Super Admins               a refund is past its target time
    settlement_completed      -> the merchant's admins          a payout was made
    tax_rule_updated          -> the Super Admins               a tax rule was created, changed or switched off

Sent after the change is committed, from a background thread, and never able to break the API call that triggered it. Each
delivery is claimed first with a unique key (notification_idempotency) so a retry cannot send it twice. Amounts appear in the
message in the currency the customer paid in. Card details never appear.
"""
from __future__ import annotations

import logging
import threading
from uuid import UUID

from app.services import notification_idempotency

logger = logging.getLogger(__name__)


def _dispatch(fn, *args, **kwargs) -> None:
    def runner():
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.exception("payment notification delivery failed")

    threading.Thread(target=runner, name="payment-notification", daemon=True).start()


def _deliver(db, *, recipients, category, title, message, metadata, dedupe_key, tenant_id=None, channels=None):
    from app.services.notification_service import create_automatic_notification

    recipients = list(dict.fromkeys(recipients or []))
    if not recipients:
        logger.warning("%s NOT delivered: no recipients (%s)", category, dedupe_key)
        return None
    if not notification_idempotency.claim(db, dedupe_key):
        logger.info("%s skipped: already delivered (%s)", category, dedupe_key)
        return None
    payload = {k: v for k, v in {**metadata, "category": category}.items() if v is not None}
    try:
        return create_automatic_notification(
            db, title=title, message=message, category=category, user_ids=recipients, tenant_id=tenant_id,
            metadata=payload, channels=channels or ["in_app", "push"],
        )
    except Exception:
        logger.exception("%s delivery failed; releasing claim %s", category, dedupe_key)
        db.rollback()
        notification_idempotency.release(db, dedupe_key)
        return None


def _merchant_admins(db, tenant_id, access_token=None) -> list[UUID]:
    """The Enterprise Admins of the merchant's business."""
    from types import SimpleNamespace

    from app.services import event_notification_service as recipients_of

    if tenant_id is None:
        return []
    try:
        admins, _ = recipients_of.resolve_enterprise_admin_user_ids(db, SimpleNamespace(enterprise=None, tenant_id=tenant_id), access_token=access_token)
        return admins
    except Exception:
        logger.exception("could not look up the admins of tenant %s", tenant_id)
        return []


def _platform_admins(access_token=None) -> list[UUID]:
    from app.services import event_notification_service as recipients_of

    try:
        return recipients_of.resolve_platform_admin_user_ids(access_token)
    except Exception:
        logger.exception("could not look up the platform admins")
        return []


def _stamp(value) -> str:
    return value.isoformat() if value else ""


def _amount(db, amount, currency) -> str:
    from app.services import currency_service
    from app.utils.money import money_str

    return f"{currency} {money_str(amount, currency_service.decimals_for(db, currency))}"


# --- payments -------------------------------------------------------------------------------------------------------

def _payment_meta(payment, **extra) -> dict:
    return {
        "payment_id": str(payment.id), "entity_type": "payment", "entity_id": str(payment.id), "status": payment.status,
        "purchase_type": payment.purchase_type, "purchase_id": payment.purchase_id, "amount": format(payment.charge_total, "f"),
        "currency": payment.charge_currency, "invoice_id": str(payment.invoice_id) if payment.invoice_id else None, **extra,
    }


def notify_payment_result(payment_id, *, access_token: str | None = None) -> None:
    """Call after a payment has been committed as succeeded or failed."""
    _dispatch(_deliver_payment_result, str(payment_id), access_token)


def _deliver_payment_result(payment_id: str, access_token: str | None):
    from app.db.database import SessionLocal
    from app.models.payment_model import Payment

    with SessionLocal() as db:
        payment = db.get(Payment, UUID(payment_id))
        if payment is None or payment.customer_user_id is None:
            return None
        amount = _amount(db, payment.charge_total, payment.charge_currency)
        if payment.status in ("succeeded", "authorized"):
            _deliver(
                db, recipients=[payment.customer_user_id], category="payment_succeeded", title="Payment successful",
                message=f"Your payment of {amount} was successful.", metadata=_payment_meta(payment),
                dedupe_key=f"payment_succeeded:{payment_id}",
            )
            _deliver(
                db, recipients=_merchant_admins(db, payment.tenant_id, access_token), category="payment_received", title="Payment received",
                message=f"A customer paid {amount}.", metadata=_payment_meta(payment), tenant_id=payment.tenant_id,
                dedupe_key=f"payment_received:{payment_id}",
            )
        elif payment.status == "failed":
            _deliver(
                db, recipients=[payment.customer_user_id], category="payment_failed", title="Payment failed",
                message=f"Your payment of {amount} did not go through. You can try again.",
                metadata=_payment_meta(payment, failure_message=payment.failure_message, can_retry=True),
                dedupe_key=f"payment_failed:{payment_id}:{_stamp(payment.failed_at)}",
            )


def notify_payment_review(payment_id, *, access_token: str | None = None) -> None:
    _dispatch(_deliver_payment_review, str(payment_id), access_token)


def _deliver_payment_review(payment_id: str, access_token: str | None):
    from app.db.database import SessionLocal
    from app.models.payment_model import Payment

    with SessionLocal() as db:
        payment = db.get(Payment, UUID(payment_id))
        if payment is None:
            return None
        reasons = ", ".join(payment.risk_reasons or []) or "the gateway did not answer in time"
        return _deliver(
            db, recipients=_platform_admins(access_token), category="payment_review_required", title="Payment needs a decision",
            message=f"A payment of {_amount(db, payment.charge_total, payment.charge_currency)} is held: {reasons}.",
            metadata=_payment_meta(payment, risk_score=payment.risk_score), dedupe_key=f"payment_review:{payment_id}:{payment.status}",
        )


def notify_retry_reminder(payment_id) -> None:
    _dispatch(_deliver_retry_reminder, str(payment_id))


def _deliver_retry_reminder(payment_id: str):
    from app.db.database import SessionLocal
    from app.models.payment_model import Payment

    with SessionLocal() as db:
        payment = db.get(Payment, UUID(payment_id))
        if payment is None or payment.customer_user_id is None:
            return None
        return _deliver(
            db, recipients=[payment.customer_user_id], category="payment_retry_reminder", title="Finish your payment",
            message=f"Your payment of {_amount(db, payment.charge_total, payment.charge_currency)} did not go through. Try again to complete it.",
            metadata=_payment_meta(payment, can_retry=True), dedupe_key=f"payment_retry_reminder:{payment_id}",
        )


# --- invoices --------------------------------------------------------------------------------------------------------

def notify_invoice(invoice_id, *, email: bool = False, access_token: str | None = None) -> None:
    """The customer is told an invoice exists. With `email` the notification also goes by e-mail."""
    _dispatch(_deliver_invoice, str(invoice_id), email)


def _deliver_invoice(invoice_id: str, email: bool):
    from app.db.database import SessionLocal
    from app.models.payment_model import Invoice

    with SessionLocal() as db:
        invoice = db.get(Invoice, UUID(invoice_id))
        if invoice is None or invoice.customer_user_id is None:
            return None
        kind = {"credit_note": "credit note", "debit_note": "debit note"}.get(invoice.doc_type, "invoice")
        meta = {"invoice_id": invoice_id, "entity_type": "invoice", "entity_id": invoice_id, "invoice_number": invoice.number,
                "payment_id": str(invoice.payment_id), "doc_type": invoice.doc_type, "amount": format(invoice.total, "f"),
                "currency": invoice.currency, "download_path": f"/api/v1/invoices/{invoice_id}/download"}
        channels = ["in_app", "push", "email"] if email else ["in_app", "push"]
        return _deliver(
            db, recipients=[invoice.customer_user_id], category="invoice_generated", title=f"Your {kind} {invoice.number}",
            message=f"Your {kind} {invoice.number} for {_amount(db, invoice.total, invoice.currency)} is ready. You can download it from your invoices.",
            metadata=meta, channels=channels, dedupe_key=f"invoice_generated:{invoice_id}:{invoice.email_count if email else 0}",
        )


# --- refunds ---------------------------------------------------------------------------------------------------------

def _refund_meta(refund, payment, **extra) -> dict:
    return {
        "refund_id": str(refund.id), "payment_id": str(refund.payment_id), "entity_type": "refund", "entity_id": str(refund.id),
        "status": refund.status, "amount": format(refund.amount, "f"), "currency": refund.currency,
        "purchase_type": payment.purchase_type if payment else None, "purchase_id": payment.purchase_id if payment else None, **extra,
    }


def notify_refund(refund_id, outcome: str, *, access_token: str | None = None) -> None:
    """`outcome`: requested (to the merchant), approved / rejected / completed (to the payer), escalated (to the Super Admins)."""
    _dispatch(_deliver_refund, str(refund_id), outcome, access_token)


def _deliver_refund(refund_id: str, outcome: str, access_token: str | None):
    from app.db.database import SessionLocal
    from app.models.payment_model import Payment, Refund

    with SessionLocal() as db:
        refund = db.get(Refund, UUID(refund_id))
        if refund is None:
            return None
        payment = db.get(Payment, refund.payment_id)
        amount = _amount(db, refund.amount, refund.currency)
        payer = [payment.customer_user_id] if payment is not None and payment.customer_user_id else []
        key = f"refund_{outcome}:{refund_id}"
        if outcome == "requested":
            return _deliver(
                db, recipients=_merchant_admins(db, refund.tenant_id, access_token), category="refund_requested", title="Refund requested",
                message=f"A customer asked for a refund of {amount}.", metadata=_refund_meta(refund, payment, reason=refund.reason),
                tenant_id=refund.tenant_id, dedupe_key=key,
            )
        if outcome == "approved":
            return _deliver(db, recipients=payer, category="refund_approved", title="Refund approved",
                            message=f"Your refund of {amount} was approved and is being processed.",
                            metadata=_refund_meta(refund, payment), dedupe_key=key)
        if outcome == "rejected":
            return _deliver(db, recipients=payer, category="refund_rejected", title="Refund not approved",
                            message=f"Your refund request of {amount} was not approved.",
                            metadata=_refund_meta(refund, payment, reason=refund.decision_reason), dedupe_key=key)
        if outcome == "completed":
            destination = {"original": "your original payment method", "bank_transfer": "your bank account", "wallet": "your wallet"}.get(refund.destination, "you")
            return _deliver(db, recipients=payer, category="refund_completed", title="Refund completed",
                            message=f"Your refund of {amount} was sent to {destination}.",
                            metadata=_refund_meta(refund, payment, destination=refund.destination), dedupe_key=key)
        if outcome == "escalated":
            return _deliver(db, recipients=_platform_admins(access_token), category="refund_escalated", title="Refund is overdue",
                            message=f"A refund of {amount} is past its target time and is still not completed.",
                            metadata=_refund_meta(refund, payment, sla_due_at=_stamp(refund.sla_due_at)), dedupe_key=key)
        return None


# --- settlements -------------------------------------------------------------------------------------------------------

def notify_settlement_completed(settlement_id, *, access_token: str | None = None) -> None:
    _dispatch(_deliver_settlement, str(settlement_id), access_token)


def _deliver_settlement(settlement_id: str, access_token: str | None):
    from app.db.database import SessionLocal
    from app.models.payment_model import MerchantSettlement

    with SessionLocal() as db:
        row = db.get(MerchantSettlement, UUID(settlement_id))
        if row is None or row.status != "paid":
            return None
        return _deliver(
            db, recipients=_merchant_admins(db, row.tenant_id, access_token), category="settlement_completed", title="Payout sent",
            message=f"Payout {row.reference} of {_amount(db, row.net_amount, row.currency)} was sent.",
            metadata={"settlement_id": settlement_id, "entity_type": "settlement", "entity_id": settlement_id, "reference": row.reference,
                      "net_amount": format(row.net_amount, "f"), "currency": row.currency, "payout_reference": row.payout_reference,
                      "period_start": _stamp(row.period_start), "period_end": _stamp(row.period_end)},
            tenant_id=row.tenant_id, dedupe_key=f"settlement_completed:{settlement_id}",
        )


# --- tax rules ----------------------------------------------------------------------------------------------------------

def notify_tax_rule_updated(rule_id, action: str, actor_name: str | None, *, access_token: str | None = None) -> None:
    """`action`: created, changed or deactivated."""
    _dispatch(_deliver_tax_rule, str(rule_id), action, actor_name, access_token)


def _deliver_tax_rule(rule_id: str, action: str, actor_name: str | None, access_token: str | None):
    from app.db.database import SessionLocal
    from app.models.payment_model import TaxRule

    with SessionLocal() as db:
        rule = db.get(TaxRule, UUID(rule_id))
        if rule is None:
            return None
        place = rule.country + (f"/{rule.state}" if rule.state else "")
        return _deliver(
            db, recipients=_platform_admins(access_token), category="tax_rule_updated", title="Tax rule updated",
            message=f"{rule.name} ({place}, {format(rule.rate, 'f')}%) was {action}" + (f" by {actor_name}." if actor_name else "."),
            metadata={"rule_id": rule_id, "entity_type": "tax_rule", "entity_id": rule_id, "rule_key": str(rule.rule_key), "version": rule.version,
                      "tax_type": rule.tax_type, "name": rule.name, "country": rule.country, "state": rule.state, "rate": format(rule.rate, "f"),
                      "action": action, "actor_name": actor_name},
            dedupe_key=f"tax_rule_updated:{rule_id}:{action}",
        )
