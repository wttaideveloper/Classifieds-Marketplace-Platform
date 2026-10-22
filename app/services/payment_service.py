"""Payments: pricing (tax + currency), creating a payment at the gateway, verifying it, retrying, cancelling, capturing, the
gateway's webhooks, and checking payments whose answer was lost.

Status of a payment:

    created           made at the gateway; the customer has not paid yet            -> verify / cancel / expires
    requires_action   3-D Secure / OTP is still to be done                          -> verify again after the step
    processing        sent; the gateway has not given a final answer                -> checked automatically
    authorized        approved, money held, not captured (capture_method=manual)    -> capture / cancel
    succeeded         paid: the invoice exists, the order is updated                -> refund
    partially_refunded / refunded
    failed            the gateway refused it                                        -> retry
    cancelled / expired
    review            held for a Super Admin: the risk check, or money that arrived after the payment was closed

The amount is never taken from the client. The exchange rate and the tax rules used are kept on the payment, so a charge can be
explained whatever the rates and rules become later. The gateway is only ever asked for the amount stored here.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.payment_model import Payment, PaymentTransaction, SavedPaymentMethod, WebhookEvent
from app.services import currency_service, finance_audit, payment_notifications, payment_payables, payment_risk, tax_service
from app.services.payment_gateways import get_gateway
from app.services.payment_gateways import base as gw
from app.utils.money import from_minor, quantize, to_decimal, to_minor

logger = logging.getLogger(__name__)

STATUS_LABELS = {
    "created": "Waiting for payment", "requires_action": "Needs authentication", "processing": "Processing", "authorized": "Authorized",
    "succeeded": "Paid", "failed": "Failed", "cancelled": "Cancelled", "expired": "Expired", "review": "Under review",
    "partially_refunded": "Partially refunded", "refunded": "Refunded",
}
OPEN = ("created", "requires_action", "processing", "authorized", "review")
PAID = ("succeeded", "partially_refunded", "refunded")
CLOSED_WITHOUT_MONEY = ("failed", "cancelled", "expired")
PAYABLE_LINK_LIFETIME = timedelta(minutes=30)


def _now() -> datetime:
    return datetime.utcnow()


def _err(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


# ---------------------------------------------------------------- pricing

@dataclass
class Pricing:
    calc: dict
    charge_currency: str
    rate: Decimal
    rate_snapshot: dict
    base_rate: Decimal
    base_currency: str
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    base_total: Decimal
    lines: list[dict]
    breakdown: list[dict]
    decimals: int
    warnings: list[str] = field(default_factory=list)


def build_pricing(db: Session, payable, billing: dict, charge_currency: str, payer_user_id: UUID | None, *, enforce_age: bool = True) -> Pricing:
    """Tax in the item currency, then each amount converted once to the currency the customer pays in and rounded there."""
    item_currency = payable.currency
    currency_service.require_active_currency(db, item_currency)
    currency_service.require_active_currency(db, charge_currency)
    country = (billing.get("country") or payable.country or "").upper()
    if not country:
        raise _err(422, "billing.country is required to work out the tax")
    items = [tax_service.TaxItemIn(unit_amount=l.unit_amount, quantity=l.quantity, category=l.category, description=l.description, ref=f"line-{i + 1}")
             for i, l in enumerate(payable.lines)]
    ctx = tax_service.build_context(
        db, currency=item_currency, customer_country=country, customer_state=billing.get("state") or payable.state, customer_tax_id=billing.get("tax_id"),
        customer_is_business=bool(billing.get("is_business")), tenant_id=payable.tenant_id, user_id=payer_user_id,
        prices_include_tax=payable.prices_include_tax,
    )
    calc = tax_service.calculate(db, items, ctx)

    rate, snapshot = currency_service.rate_between(db, item_currency, charge_currency, enforce_age=enforce_age)
    decimals = currency_service.decimals_for(db, charge_currency)
    lines, breakdown_map = [], {}
    for line in calc["lines"]:
        net_c = quantize(line["net_amount"] * rate, decimals)
        comps, tax_c = [], Decimal(0)
        for comp in line["taxes"]:
            amount_c = quantize(comp["tax_amount"] * rate, decimals)
            tax_c += amount_c
            comps.append({**comp, "tax_amount_charge": amount_c, "taxable_amount_charge": net_c})
            key = (comp["tax_type"], comp["name"], comp["rate"])
            row = breakdown_map.setdefault(key, {"tax_type": comp["tax_type"], "name": comp["name"], "rate": comp["rate"], "country": country,
                                                 "state": billing.get("state") or payable.state, "taxable_amount": Decimal(0), "tax_amount": Decimal(0)})
            row["taxable_amount"] += net_c
            row["tax_amount"] += amount_c
        lines.append({
            "ref": line["ref"], "description": line["description"], "category": line["category"], "quantity": line["quantity"],
            "unit_amount": line["unit_amount"], "net_amount": line["net_amount"], "tax_amount": line["tax_amount"], "gross_amount": line["gross_amount"],
            "charge_net_amount": net_c, "charge_tax_amount": tax_c, "charge_gross_amount": net_c + tax_c, "taxes": comps,
        })
    subtotal = sum((l["charge_net_amount"] for l in lines), Decimal(0))
    tax = sum((l["charge_tax_amount"] for l in lines), Decimal(0))
    base = currency_service.base_currency()
    base_rate, _ = currency_service.rate_between(db, charge_currency, base, enforce_age=enforce_age)
    total = subtotal + tax
    return Pricing(
        calc=calc, charge_currency=charge_currency, rate=rate, rate_snapshot=snapshot, base_rate=base_rate, base_currency=base, subtotal=subtotal, tax=tax,
        total=total, base_total=quantize(total * base_rate, currency_service.decimals_for(db, base)), lines=lines,
        breakdown=list(breakdown_map.values()), decimals=decimals, warnings=calc.get("warnings", []),
    )


def _billing(actor, payable, supplied) -> dict:
    billing = supplied.model_dump(exclude_none=True) if supplied is not None else {}
    billing.setdefault("name", actor.name)
    billing.setdefault("email", payable.payer_email or actor.email)
    if not billing.get("country") and payable.country:
        billing["country"] = payable.country
    if not billing.get("state") and payable.state:
        billing["state"] = payable.state
    return billing


def quote(db: Session, actor, body) -> tuple[Pricing, "payment_payables.Payable"]:
    """What the customer would pay, shown before they confirm. Nothing is saved and no gateway is called."""
    payable = payment_payables.resolve(db, actor, body)
    charge_currency = (body.currency or payable.currency).upper()
    payer = payable.payer_user_id or actor.user_uuid
    pricing = build_pricing(db, payable, _billing(actor, payable, body.billing), charge_currency, payer)
    _check_payable_amount(db, pricing)
    return pricing, payable


def _check_payable_amount(db: Session, pricing: Pricing) -> None:
    if pricing.total <= 0:
        raise _err(422, "There is nothing to pay on this order")
    currency_service.check_amount_limits(db, pricing.charge_currency, pricing.total)


# ---------------------------------------------------------------- access

def is_payer(actor, payment: Payment) -> bool:
    return payment.customer_user_id is not None and str(payment.customer_user_id) == actor.id


def can_view(actor, payment: Payment) -> bool:
    return actor.is_super_admin or is_payer(actor, payment) or (actor.role == "merchant" and payment.tenant_id is not None and payment.tenant_id == actor.tenant_id)


def get_visible(db: Session, actor, payment_id: UUID) -> Payment:
    payment = db.get(Payment, payment_id)
    if payment is None or not can_view(actor, payment):
        raise _err(404, "Payment not found")          # someone else's payment looks exactly like one that does not exist
    return payment


def _manages(actor, payment: Payment) -> bool:
    return actor.is_super_admin or (actor.role == "merchant" and payment.tenant_id == actor.tenant_id)


def actions_for(actor, payment: Payment) -> list[str]:
    out, payer, manager, s = [], is_payer(actor, payment), _manages(actor, payment), payment.status
    if payer and s in ("created", "requires_action", "processing"):
        out.append("verify")
    if payer and s in ("created", "requires_action"):
        out.append("checkout")
    if (payer or manager) and s in ("created", "requires_action", "authorized"):
        out.append("cancel")
    if payer and s in ("failed", "expired"):
        out.append("retry")
    if manager and s == "authorized":
        out.append("capture")
    if actor.is_super_admin and s == "review":
        out.append("resolve")
    if s in ("succeeded", "partially_refunded") and to_decimal(payment.charge_total) - to_decimal(payment.refunded_amount) > 0 and (payer or manager):
        out.append("refund")
    if payment.invoice_id and s in PAID:
        out.append("download_invoice")
    return out


# ---------------------------------------------------------------- creating

def _gateway_request(db: Session, payment: Payment, *, amount_minor: int | None = None, saved: SavedPaymentMethod | None = None,
                     return_url: str | None = None) -> gw.GatewayRequest:
    decimals = currency_service.decimals_for(db, payment.charge_currency)
    billing = payment.billing or {}
    customer = gw.GatewayCustomer(user_id=str(payment.customer_user_id) if payment.customer_user_id else None, email=payment.customer_email or billing.get("email"),
                                  name=billing.get("name"), gateway_customer_id=saved.gateway_customer_id if saved else None)
    return gw.GatewayRequest(
        payment_id=str(payment.id), amount_minor=amount_minor if amount_minor is not None else to_minor(to_decimal(payment.charge_total), decimals),
        currency=payment.charge_currency, decimals=decimals, capture_method=payment.capture_method, description=(payment.extra or {}).get("description", ""),
        customer=customer, saved_token=saved.token if saved else None, return_url=return_url, idempotency_key=f"pay-{payment.id}-{payment.attempt_no}",
        gateway_order_id=payment.gateway_order_id, gateway_payment_id=payment.gateway_payment_id,
        metadata={"purchase_type": payment.purchase_type, "purchase_id": payment.purchase_id},
    )


def _log(db: Session, payment: Payment, kind: str, *, status: str | None = None, ref: str | None = None, amount=None, error: str | None = None, detail=None) -> None:
    db.add(PaymentTransaction(payment_id=payment.id, kind=kind, status=status, gateway_ref=ref, amount=amount, currency=payment.charge_currency,
                              error=(error or None) and error[:255], detail=finance_audit.jsonable(detail) if detail else None))


def _saved_method(db: Session, actor, method_id: UUID | None) -> SavedPaymentMethod | None:
    if method_id is None:
        return None
    method = db.get(SavedPaymentMethod, method_id)
    if method is None or not method.is_active or str(method.user_id) != actor.id:
        raise _err(404, "Saved payment method not found")
    if method.provider != settings.PAYMENT_GATEWAY:
        raise _err(409, "This saved method belongs to another payment gateway. Add a new one")
    return method


def create_payment(db: Session, actor, body, idempotency_key: str | None = None, *, access_token: str | None = None) -> tuple[Payment, dict]:
    key = f"{actor.id}:{idempotency_key.strip()}" if idempotency_key and idempotency_key.strip() else None
    if key:
        if len(idempotency_key.strip()) > 60:
            raise _err(422, "The Idempotency-Key can have at most 60 characters")
        previous = db.query(Payment).filter(Payment.idempotency_key == key).first()
        if previous is not None:
            if previous.purchase_type != body.purchase_type or (body.purchase_id and previous.purchase_id != body.purchase_id):
                raise _err(409, "This Idempotency-Key was already used for a different payment")
            return previous, (previous.extra or {}).get("client_params", {})
    payable = payment_payables.resolve(db, actor, body)
    resumed = _resume_or_block(db, actor, payable, body)
    if resumed is not None:
        return resumed, (resumed.extra or {}).get("client_params", {})
    return _create(db, actor, payable, body, key=key, access_token=access_token)


def _resume_or_block(db: Session, actor, payable, body) -> Payment | None:
    existing = db.query(Payment).filter(Payment.purchase_type == payable.purchase_type, Payment.purchase_id == payable.purchase_id,
                                        Payment.status.in_(OPEN + PAID)).order_by(Payment.created_at.desc()).all()
    for payment in existing:
        if payment.status in PAID:
            raise _err(409, "This order is already paid")
    for payment in existing:
        charge_currency = (body.currency or payable.currency).upper()
        if payment.status in ("created", "requires_action") and payment.charge_currency == charge_currency and (payment.expires_at is None or payment.expires_at > _now()) \
                and body.saved_method_id is None and is_payer(actor, payment):
            return payment                                   # the customer comes back to the payment they started
        if payment.status in ("processing", "authorized", "review"):
            raise _err(409, f"A payment for this order is already {STATUS_LABELS[payment.status].lower()}")
        payment.status, payment.cancelled_at = "cancelled", _now()     # a different currency or an old attempt: start again
        _log(db, payment, "cancel", status="cancelled", error="Replaced by a new payment")
    return None


def _create(db: Session, actor, payable, body, *, key: str | None, attempt_no: int = 1, retry_of: Payment | None = None,
            access_token: str | None = None) -> tuple[Payment, dict]:
    if attempt_no > settings.PAYMENT_MAX_ATTEMPTS:
        raise _err(429, f"This order has had {settings.PAYMENT_MAX_ATTEMPTS} payment attempts. Contact support")
    billing = _billing(actor, payable, body.billing)
    charge_currency = (body.currency or payable.currency).upper()
    payer = payable.payer_user_id or actor.user_uuid
    pricing = build_pricing(db, payable, billing, charge_currency, payer)
    _check_payable_amount(db, pricing)
    saved = _saved_method(db, actor, getattr(body, "saved_method_id", None))
    risk = payment_risk.assess(db, user_id=payer, email=payable.payer_email, base_amount=pricing.base_total, charge_currency=charge_currency, billing=billing)

    payment = Payment(
        tenant_id=payable.tenant_id, customer_user_id=payer, customer_email=(payable.payer_email or actor.email), purchase_type=payable.purchase_type,
        purchase_id=payable.purchase_id, attempt_no=attempt_no, retry_of_id=retry_of.id if retry_of else None, idempotency_key=key, status="created",
        capture_method=getattr(body, "capture_method", "automatic") if payable.purchase_type == "custom" else "automatic",
        provider=settings.PAYMENT_GATEWAY, item_currency=payable.currency, subtotal_amount=pricing.calc["subtotal"], tax_amount=pricing.calc["tax_total"],
        total_amount=pricing.calc["total"], charge_currency=charge_currency, charge_subtotal=pricing.subtotal, charge_tax=pricing.tax, charge_total=pricing.total,
        exchange_rate=pricing.rate, base_currency=pricing.base_currency, base_total=pricing.base_total, base_rate=pricing.base_rate,
        rate_snapshot=finance_audit.jsonable(pricing.rate_snapshot), billing=billing, lines=finance_audit.jsonable(pricing.lines),
        tax_details=finance_audit.jsonable({"calc": pricing.calc, "breakdown": pricing.breakdown, "reverse_charge": pricing.calc["reverse_charge"],
                                            "is_exempt": pricing.calc["is_exempt"], "note": pricing.calc["note"], "warnings": pricing.warnings}),
        risk_score=risk.score, risk_decision=risk.decision, risk_reasons=risk.reasons, expires_at=_now() + PAYABLE_LINK_LIFETIME,
        extra={"description": payable.description, "payable": payment_payables.to_stored(payable), "created_by": actor.id},
        saved_method_id=saved.id if saved else None, method_brand=saved.brand if saved else None, method_last4=saved.last4 if saved else None,
    )
    db.add(payment)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise _err(409, "This Idempotency-Key was already used. Send the request again to get the earlier payment")

    if risk.decision == "deny":
        payment.status, payment.failed_at, payment.failure_code, payment.failure_message = "failed", _now(), "risk_denied", "This payment was declined by our security check"
        _log(db, payment, "create", status="failed", error="risk_denied", detail={"score": risk.score, "reasons": risk.reasons})
        db.commit()
        raise _err(403, "This payment was declined by our security check")
    if risk.decision == "review":
        payment.status, payment.failure_code, payment.failure_message = "review", "risk_review", "This payment is being checked by our team"
        _log(db, payment, "create", status="review", detail={"score": risk.score, "reasons": risk.reasons})
        db.commit()
        payment_notifications.notify_payment_review(payment.id, access_token=access_token)
        return payment, {}

    return _start_at_gateway(db, payment, saved, getattr(body, "return_url", None), access_token)


def _start_at_gateway(db: Session, payment: Payment, saved: SavedPaymentMethod | None, return_url: str | None, access_token: str | None) -> tuple[Payment, dict]:
    request = _gateway_request(db, payment, saved=saved, return_url=return_url)
    try:
        result = get_gateway(payment.provider).create(request)
    except gw.GatewayError as exc:
        payment.status, payment.failed_at, payment.failure_code, payment.failure_message = "failed", _now(), exc.code, exc.message[:255]
        _log(db, payment, "create", status="failed", error=exc.message)
        db.commit()
        raise _err(504 if isinstance(exc, gw.GatewayTimeout) else 502, "The payment gateway is not available right now. Try again shortly" if exc.retryable else exc.message)
    payment.gateway_order_id = result.gateway_order_id
    payment.extra = {**(payment.extra or {}), "client_params": result.client_params}
    _log(db, payment, "create", status=result.status, ref=result.gateway_order_id, amount=payment.charge_total)
    if result.status != gw.REQUIRES_PAYMENT_METHOD:                  # a saved method can be charged straight away
        apply_result(db, payment, result, "create", access_token=access_token)
    db.commit()
    db.refresh(payment)
    _after_commit(payment, access_token)
    return payment, result.client_params


def get_checkout(db: Session, actor, payment_id: UUID) -> tuple[Payment, dict]:
    payment = get_visible(db, actor, payment_id)
    if not is_payer(actor, payment):
        raise _err(403, "Only the person who is paying can open the checkout")
    if payment.status not in ("created", "requires_action"):
        raise _err(409, f"This payment is {STATUS_LABELS[payment.status].lower()}")
    if payment.expires_at is not None and payment.expires_at <= _now():
        raise _err(409, "This payment has expired. Start a new one")
    params = (payment.extra or {}).get("client_params", {})
    if payment.provider == "stripe" and settings.STRIPE_PUBLISHABLE_KEY:
        params = {**params, "publishable_key": settings.STRIPE_PUBLISHABLE_KEY}
    return payment, params


# ---------------------------------------------------------------- the state machine

def apply_result(db: Session, payment: Payment, result: gw.GatewayResult, source: str, *, expected_minor: int | None = None,
                 access_token: str | None = None) -> Payment:
    """Brings the payment in line with what the gateway says. Safe to call twice with the same answer."""
    if result.gateway_order_id and not payment.gateway_order_id:
        payment.gateway_order_id = result.gateway_order_id
    if result.gateway_payment_id:
        payment.gateway_payment_id = result.gateway_payment_id
    if result.method_brand:
        payment.method_brand, payment.method_last4 = result.method_brand, result.method_last4
    decimals = currency_service.decimals_for(db, payment.charge_currency)
    if result.fee_minor is not None:
        payment.gateway_fee = from_minor(result.fee_minor, decimals)
    payment.last_gateway_check_at = _now()
    before = payment.status
    status = result.status

    if payment.status in PAID:
        _log(db, payment, source, status=status, ref=result.gateway_payment_id, detail={"note": "already paid; nothing changed"})
        return payment
    if status in (gw.SUCCEEDED, gw.AUTHORIZED):
        wrong_amount = expected_minor is not None and expected_minor != to_minor(to_decimal(payment.charge_total), decimals)
        duplicate = db.query(Payment).filter(Payment.purchase_type == payment.purchase_type, Payment.purchase_id == payment.purchase_id,
                                             Payment.id != payment.id, Payment.status.in_(PAID)).first() is not None
        if wrong_amount or duplicate or payment.status in ("cancelled", "expired"):
            payment.status, payment.needs_reconciliation = "review", True
            payment.failure_code = "amount_mismatch" if wrong_amount else "duplicate_payment" if duplicate else "late_payment"
            payment.failure_message = ("The amount received is not the amount expected" if wrong_amount
                                       else "This order was already paid by another payment" if duplicate else "Money arrived after this payment was closed")
            _log(db, payment, source, status="review", ref=result.gateway_payment_id, error=payment.failure_message)
            db.flush()
            payment_notifications.notify_payment_review(payment.id, access_token=access_token)
            return payment
        if status == gw.AUTHORIZED:
            payment.status, payment.authorized_at, payment.next_action = "authorized", payment.authorized_at or _now(), None
        else:
            payment.status, payment.next_action = "succeeded", None
            finalize_success(db, payment)
        payment.needs_reconciliation, payment.failure_code, payment.failure_message = False, None, None
    elif status == gw.REQUIRES_ACTION:
        payment.status, payment.next_action = "requires_action", result.next_action
    elif status == gw.PROCESSING:
        payment.status = "processing"
    elif status == gw.FAILED:
        payment.status, payment.failed_at, payment.needs_reconciliation = "failed", _now(), False
        payment.failure_code, payment.failure_message, payment.next_action = result.failure_code, (result.failure_message or "The payment failed")[:255], None
    elif status == gw.CANCELLED:
        payment.status, payment.cancelled_at, payment.needs_reconciliation, payment.next_action = "cancelled", _now(), False, None
    _log(db, payment, source, status=payment.status, ref=result.gateway_payment_id or result.gateway_order_id,
         error=result.failure_message if status == gw.FAILED else None, detail={"gateway_status": status, "from": before})
    return payment


def finalize_success(db: Session, payment: Payment) -> None:
    """Runs once, when the payment becomes paid: the tax is recorded, the invoice is issued and the order is updated."""
    from app.services import invoice_service

    payment.captured_at = payment.captured_at or _now()
    calc = (payment.tax_details or {}).get("calc") or {}
    tax_service.record_transactions(db, payment, calc, charge_per_item=to_decimal(payment.exchange_rate))
    invoice_service.issue_for_payment(db, payment)
    payment_payables.on_paid(db, payment)


def _after_commit(payment: Payment, access_token: str | None) -> None:
    if payment.status in ("succeeded", "failed"):
        payment_notifications.notify_payment_result(payment.id, access_token=access_token)
    if payment.status == "succeeded" and payment.invoice_id:
        payment_notifications.notify_invoice(payment.invoice_id, email=True, access_token=access_token)
    if payment.status == "review":
        payment_notifications.notify_payment_review(payment.id, access_token=access_token)


# ---------------------------------------------------------------- verify, cancel, capture, retry, resolve

def verify_payment(db: Session, actor, payment_id: UUID, client_data: dict, *, access_token: str | None = None) -> Payment:
    payment = get_visible(db, actor, payment_id)
    if not (is_payer(actor, payment) or _manages(actor, payment)):
        raise _err(403, "You cannot verify this payment")
    if payment.status in PAID or payment.status in ("authorized", "cancelled", "expired"):
        return payment
    if payment.status == "review":
        raise _err(409, "This payment is under review")
    if payment.status == "failed" and (payment.failure_code in ("risk_denied", "review_rejected") or not payment.gateway_order_id):
        return payment
    request = _gateway_request(db, payment)
    started = _now()
    try:
        result = get_gateway(payment.provider).confirm(request, client_data)
    except gw.GatewayTimeout as exc:
        payment.status, payment.needs_reconciliation = "processing", True
        payment.failure_message = "The payment gateway did not answer in time. We are checking the payment and will tell you the result"
        _log(db, payment, "timeout", status="processing", error=exc.message)
        db.commit()
        db.refresh(payment)
        return payment
    except gw.GatewayError as exc:
        _log(db, payment, "verify", status="error", error=exc.message)
        db.commit()
        raise _err(502 if exc.retryable else 422, exc.message)
    apply_result(db, payment, result, "verify", access_token=access_token)
    db.commit()
    db.refresh(payment)
    _after_commit(payment, access_token)
    logger.info("payment %s verified in %.0f ms -> %s", payment.id, (_now() - started).total_seconds() * 1000, payment.status)
    return payment


def cancel_payment(db: Session, actor, payment_id: UUID, *, access_token: str | None = None) -> Payment:
    """Cancels a payment that has not taken money (or one that is authorized and not captured yet)."""
    payment = get_visible(db, actor, payment_id)
    if not (is_payer(actor, payment) or _manages(actor, payment)):
        raise _err(403, "You cannot cancel this payment")
    if payment.status in PAID:
        raise _err(409, "This payment is already paid. Ask for a refund instead")
    if payment.status in ("cancelled", "expired", "failed"):
        return payment
    if payment.status in ("processing", "review"):
        raise _err(409, f"A payment that is {STATUS_LABELS[payment.status].lower()} cannot be cancelled")
    if payment.gateway_order_id:
        try:
            result = get_gateway(payment.provider).cancel(_gateway_request(db, payment))
        except gw.GatewayError as exc:
            raise _err(409 if exc.code in ("already_captured", "not_capturable") else 502, exc.message)
        _log(db, payment, "cancel", status=result.status, ref=payment.gateway_payment_id)
    payment.status, payment.cancelled_at, payment.next_action = "cancelled", _now(), None
    db.commit()
    db.refresh(payment)
    return payment


def capture_payment(db: Session, actor, payment_id: UUID, *, access_token: str | None = None) -> Payment:
    payment = get_visible(db, actor, payment_id)
    if not _manages(actor, payment):
        raise _err(403, "Only the business or the platform can capture a payment")
    if payment.status != "authorized":
        raise _err(409, "Only an authorized payment can be captured")
    try:
        result = get_gateway(payment.provider).capture(_gateway_request(db, payment))
    except gw.GatewayTimeout as exc:
        payment.needs_reconciliation = True
        _log(db, payment, "timeout", status=payment.status, error=exc.message)
        db.commit()
        raise _err(504, "The payment gateway did not answer. We will check the payment") from exc
    except gw.GatewayError as exc:
        raise _err(502 if exc.retryable else 409, exc.message)
    payment.status = "authorized"
    apply_result(db, payment, result, "capture", access_token=access_token)
    db.commit()
    db.refresh(payment)
    _after_commit(payment, access_token)
    return payment


def retry_payment(db: Session, actor, payment_id: UUID, body, *, access_token: str | None = None) -> tuple[Payment, dict]:
    """A new attempt for the same order after a failure. The earlier attempts stay in the history."""
    old = get_visible(db, actor, payment_id)
    if not is_payer(actor, old):
        raise _err(403, "Only the person who is paying can try again")
    if old.status not in ("failed", "expired"):
        raise _err(409, f"A payment that is {STATUS_LABELS[old.status].lower()} cannot be retried")
    if old.failure_code == "risk_denied":
        raise _err(403, "This payment was declined by our security check")
    attempts = db.query(Payment).filter(Payment.purchase_type == old.purchase_type, Payment.purchase_id == old.purchase_id).count()
    busy = db.query(Payment).filter(Payment.purchase_type == old.purchase_type, Payment.purchase_id == old.purchase_id, Payment.status.in_(OPEN + PAID)).first()
    if busy is not None:
        raise _err(409, "This order already has a payment in progress or paid")
    from app.schemas.payment_schema import Billing, PaymentRequest

    request = PaymentRequest(
        purchase_type=old.purchase_type, purchase_id=old.purchase_id, currency=body.currency or old.charge_currency, saved_method_id=body.saved_method_id,
        return_url=body.return_url, capture_method=old.capture_method,
        billing=body.billing or Billing(**{k: v for k, v in (old.billing or {}).items() if k in Billing.model_fields}),
    )
    payable = payment_payables.from_stored(old) if old.purchase_type == "custom" else payment_payables.resolve(db, actor, request)
    return _create(db, actor, payable, request, key=None, attempt_no=attempts + 1, retry_of=old, access_token=access_token)


def decide_review(db: Session, actor, payment_id: UUID, action: str, note: str | None, *, access_token: str | None = None) -> Payment:
    """A Super Admin settles a held payment. Risk hold: approve sends it to the gateway, reject fails it. Money that arrived
    late or in the wrong amount: approve accepts it as paid, reject refunds it."""
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise _err(404, "Payment not found")
    if payment.status != "review":
        raise _err(409, "This payment is not under review")
    before = {"status": payment.status, "failure_code": payment.failure_code}
    if payment.gateway_payment_id and payment.failure_code in ("late_payment", "amount_mismatch"):
        if action == "approve":
            payment.status, payment.needs_reconciliation, payment.failure_code, payment.failure_message = "succeeded", False, None, None
            finalize_success(db, payment)
        else:
            from app.services import refund_service

            payment.status = "succeeded"
            finalize_success(db, payment)
            refund_service.refund_system(db, payment, f"Refunded after review: {note or 'not accepted'}")
    elif action == "approve":
        finance_audit.record(db, actor, "payment_review_approved", "payment", payment.id, before=before, after={"status": "created"}, tenant_id=payment.tenant_id, reason=note)
        payment.status, payment.failure_code, payment.failure_message = "created", None, None
        db.commit()
        return _start_at_gateway(db, payment, None, None, access_token)[0]
    else:
        payment.status, payment.failed_at, payment.failure_code, payment.failure_message = "failed", _now(), "review_rejected", (note or "Not approved after review")[:255]
    finance_audit.record(db, actor, {"approve": "payment_review_approved", "reject": "payment_review_rejected"}[action], "payment", payment.id, before=before, after={"status": payment.status}, tenant_id=payment.tenant_id, reason=note)
    _log(db, payment, "reconcile", status=payment.status, detail={"decision": action, "by": actor.id})
    db.commit()
    db.refresh(payment)
    _after_commit(payment, access_token)
    return payment


# ---------------------------------------------------------------- listing

def list_payments(db: Session, actor, *, status: str | None = None, currency: str | None = None, purchase_type: str | None = None,
                  purchase_id: str | None = None, tenant_id: UUID | None = None, customer_user_id: UUID | None = None,
                  date_from: datetime | None = None, date_to: datetime | None = None, needs_reconciliation: bool | None = None,
                  page: int = 1, page_size: int = 20) -> tuple[list[Payment], int]:
    query = db.query(Payment)
    if actor.role == "customer":
        query = query.filter(Payment.customer_user_id == actor.user_uuid)
    elif actor.role == "merchant":
        query = query.filter(or_(Payment.tenant_id == actor.tenant_id, Payment.customer_user_id == actor.user_uuid))
    else:
        if tenant_id:
            query = query.filter(Payment.tenant_id == tenant_id)
        if customer_user_id:
            query = query.filter(Payment.customer_user_id == customer_user_id)
    if status:
        query = query.filter(Payment.status == status)
    if currency:
        query = query.filter(Payment.charge_currency == currency.upper())
    if purchase_type:
        query = query.filter(Payment.purchase_type == purchase_type)
    if purchase_id:
        query = query.filter(Payment.purchase_id == purchase_id)
    if needs_reconciliation is not None and actor.role != "customer":
        query = query.filter(Payment.needs_reconciliation.is_(needs_reconciliation))
    if date_from:
        query = query.filter(Payment.created_at >= date_from)
    if date_to:
        query = query.filter(Payment.created_at <= date_to)
    total = query.count()
    return query.order_by(Payment.created_at.desc(), Payment.id).offset((page - 1) * page_size).limit(page_size).all(), total


def transactions_of(db: Session, payment: Payment) -> list[PaymentTransaction]:
    return db.query(PaymentTransaction).filter(PaymentTransaction.payment_id == payment.id).order_by(PaymentTransaction.created_at, PaymentTransaction.id).all()


# ---------------------------------------------------------------- webhooks

EVENT_STATUS = {"payment.succeeded": gw.SUCCEEDED, "payment.authorized": gw.AUTHORIZED, "payment.failed": gw.FAILED, "payment.cancelled": gw.CANCELLED}


def handle_webhook(db: Session, provider: str, headers: dict, raw_body: bytes) -> dict:
    """Receives the gateway's asynchronous news. The signature is checked before anything is read; each event is handled once."""
    from app.services import refund_service

    if provider != settings.PAYMENT_GATEWAY:
        raise _err(404, "That gateway is not in use")
    try:
        events = get_gateway(provider).parse_webhook(headers, raw_body)
    except gw.WebhookSignatureError:
        raise _err(401, "Invalid webhook signature")
    except gw.GatewayError as exc:
        raise _err(404, exc.message)
    except (ValueError, KeyError):
        raise _err(400, "Unreadable webhook body")
    counts = {"received": len(events), "processed": 0, "duplicates": 0, "ignored": 0}
    for event in events:
        row = WebhookEvent(provider=provider, event_id=event.event_id, event_type=event.type, payload=finance_audit.jsonable(event.raw))
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            counts["duplicates"] += 1
            continue
        try:
            payment = _apply_event(db, event, refund_service)
            row.processed_at = _now()
            if payment is None:
                row.error = "no matching payment"
                counts["ignored"] += 1
            else:
                counts["processed"] += 1
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.exception("webhook event %s could not be applied", event.event_id)
            stale = db.query(WebhookEvent).filter_by(provider=provider, event_id=event.event_id).first()
            if stale is not None:
                db.delete(stale)              # not recorded as handled, so the gateway's retry is processed again
                db.commit()
            raise _err(500, "The webhook could not be processed. Please retry") from exc
        if payment is not None:
            db.refresh(payment)
            _after_commit(payment, None)
    return counts


def _find_payment(db: Session, event: gw.WebhookEvent) -> Payment | None:
    query = db.query(Payment)
    if event.gateway_order_id:
        found = query.filter(Payment.gateway_order_id == event.gateway_order_id).order_by(Payment.created_at.desc()).first()
        if found:
            return found
    if event.gateway_payment_id:
        return query.filter(Payment.gateway_payment_id == event.gateway_payment_id).order_by(Payment.created_at.desc()).first()
    return None


def _apply_event(db: Session, event: gw.WebhookEvent, refund_service) -> Payment | None:
    """The payment the event was about, or None when it matched nothing."""
    if event.type.startswith("refund."):
        return refund_service.apply_gateway_refund_event(db, event)
    status = EVENT_STATUS.get(event.type)
    payment = _find_payment(db, event)
    if status is None or payment is None:
        return None
    result = gw.GatewayResult(
        status=status, gateway_order_id=event.gateway_order_id, gateway_payment_id=event.gateway_payment_id, method_brand=event.method_brand,
        method_last4=event.method_last4, fee_minor=event.fee_minor, failure_code=event.failure_code, failure_message=event.failure_message,
    )
    apply_result(db, payment, result, "webhook", expected_minor=event.amount_minor if status in (gw.SUCCEEDED, gw.AUTHORIZED) else None)
    db.flush()
    return payment


# ---------------------------------------------------------------- stale payments

def reconcile_pending(db: Session, now: datetime | None = None, *, access_token: str | None = None) -> dict:
    """Asks the gateway about payments whose answer never came, and closes the ones nobody paid. Run on a schedule."""
    now = now or _now()
    stale_before = now - timedelta(minutes=settings.PAYMENT_STALE_MINUTES)
    candidates = db.query(Payment).filter(
        or_(Payment.needs_reconciliation.is_(True),
            Payment.status.in_(("processing", "requires_action")) & (Payment.updated_at < stale_before),
            (Payment.status == "created") & (Payment.expires_at.isnot(None)) & (Payment.expires_at < now))
    ).filter(Payment.status.in_(("created", "requires_action", "processing", "review", "failed", "cancelled", "expired"))).limit(500).all()
    summary = {"checked": 0, "paid": 0, "failed": 0, "expired": 0, "review": 0, "unreachable": 0}
    changed: list[tuple[Payment, str]] = []
    for payment in candidates:
        if not payment.gateway_order_id:
            if payment.status == "created":
                payment.status = "expired"
                summary["expired"] += 1
            continue
        if payment.status in ("failed", "cancelled", "expired") and not payment.needs_reconciliation:
            continue
        summary["checked"] += 1
        try:
            result = get_gateway(payment.provider).fetch(_gateway_request(db, payment))
        except gw.GatewayError as exc:
            summary["unreachable"] += 1
            _log(db, payment, "reconcile", status="error", error=exc.message)
            continue
        before = payment.status
        changed.append((payment, before))
        if result.status in (gw.REQUIRES_PAYMENT_METHOD, gw.REQUIRES_ACTION, gw.PROCESSING) and payment.status in ("created", "requires_action", "processing"):
            if payment.expires_at is not None and payment.expires_at < now and result.status == gw.REQUIRES_PAYMENT_METHOD:
                payment.status, payment.needs_reconciliation = "expired", False
                _log(db, payment, "reconcile", status="expired")
                summary["expired"] += 1
            elif payment.status == "processing" and payment.updated_at < now - 2 * timedelta(minutes=settings.PAYMENT_STALE_MINUTES):
                payment.status, payment.failure_code, payment.failure_message = "review", "gateway_unclear", "The gateway has not given a final answer"
                summary["review"] += 1
            continue
        apply_result(db, payment, result, "reconcile", access_token=access_token)
        if payment.status == "succeeded" and before != "succeeded":
            summary["paid"] += 1
        elif payment.status == "failed":
            summary["failed"] += 1
        elif payment.status == "review":
            summary["review"] += 1
    db.commit()
    for payment, before in changed:
        db.refresh(payment)
        if payment.status != before and payment.status in ("succeeded", "failed", "review"):
            _after_commit(payment, access_token)
    return summary


def send_retry_reminders(db: Session, now: datetime | None = None) -> int:
    """One reminder, a day after a payment failed, when nobody paid that order since."""
    now = now or _now()
    sent = 0
    failed = db.query(Payment).filter(Payment.status == "failed", Payment.failed_at < now - timedelta(hours=24), Payment.failed_at > now - timedelta(days=7),
                                      Payment.retry_reminder_sent_at.is_(None), Payment.failure_code != "risk_denied").limit(500).all()
    for payment in failed:
        later = db.query(Payment).filter(Payment.purchase_type == payment.purchase_type, Payment.purchase_id == payment.purchase_id,
                                         Payment.created_at > payment.created_at).first()
        payment.retry_reminder_sent_at = now
        if later is None:
            payment_notifications.notify_retry_reminder(payment.id)
            sent += 1
    db.commit()
    return sent
