"""The Commerce dashboard in one call, and the read-only gateway status for Payment Settings.

Both are read-only. The overview adds up what the finance reports list, in the base currency so different currencies can be summed; a business
sees its own figures and the platform sees everything (and can narrow to one business with tenant_id).
"""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.payment_model import ExchangeRate, MerchantSettlement, Payment, ReconciliationItem, Refund, TaxRule
from app.services import currency_service
from app.services.payment_report_service import PAID_STATES, _require_finance, _tenant_filter
from app.utils.money import quantize, to_decimal

WAITING_STATES = ("created", "requires_action", "processing", "authorized")
CLOSED_STATES = ("cancelled", "expired")
PAYMENT_STATUSES = ("created", "requires_action", "processing", "authorized", "succeeded", "partially_refunded", "refunded", "failed", "cancelled", "expired", "review")
REFUND_STATUSES = ("requested", "approved", "processing", "completed", "failed", "rejected")
OPEN_REFUND_STATES = ("requested", "approved", "processing")
SETTLEMENT_STATUSES = ("pending", "paid", "failed", "on_hold", "cancelled")


def _money(db: Session, value, code: str | None = None) -> Decimal:
    code = code or currency_service.base_currency()
    return quantize(to_decimal(value if value is not None else 0), currency_service.decimals_for(db, code))


def overview(db: Session, actor, *, tenant_id: UUID | None = None, date_from: datetime | None = None, date_to: datetime | None = None) -> dict:
    _require_finance(actor)
    now = datetime.utcnow()
    base = currency_service.base_currency()

    # ---- payments
    pay = _tenant_filter(db.query(Payment.status, func.count(Payment.id), func.sum(Payment.base_total), func.sum(Payment.refunded_amount * Payment.base_rate),
                                  func.sum(Payment.charge_tax * Payment.base_rate)), Payment, actor, tenant_id)
    if date_from:
        pay = pay.filter(Payment.created_at >= date_from)
    if date_to:
        pay = pay.filter(Payment.created_at <= date_to)
    by_status = {s: 0 for s in PAYMENT_STATUSES}
    collected = refunded_on_payments = tax_collected = Decimal(0)
    for status, count, base_total, refunded, tax in pay.group_by(Payment.status).all():
        by_status[status] = by_status.get(status, 0) + int(count)
        if status in PAID_STATES:
            collected += to_decimal(base_total or 0)
            refunded_on_payments += to_decimal(refunded or 0)
            tax_collected += to_decimal(tax or 0)
    total = sum(by_status.values())
    paid = sum(by_status[s] for s in PAID_STATES)
    attention = _tenant_filter(db.query(func.count(Payment.id)), Payment, actor, tenant_id)
    needs_reconciliation = attention.filter(Payment.needs_reconciliation.is_(True)).scalar() or 0
    waiting_settlement = _tenant_filter(db.query(func.count(Payment.id), func.sum(Payment.base_total)), Payment, actor, tenant_id).filter(
        Payment.status.in_(PAID_STATES), Payment.settlement_id.is_(None))
    if date_from:
        waiting_settlement = waiting_settlement.filter(Payment.created_at >= date_from)
    if date_to:
        waiting_settlement = waiting_settlement.filter(Payment.created_at <= date_to)
    awaiting_count, awaiting_base = waiting_settlement.one()

    # ---- refunds
    ref = _tenant_filter(db.query(Refund.status, func.count(Refund.id), func.sum(Refund.amount * Payment.base_rate), func.sum(Refund.tax_refunded * Payment.base_rate))
                         .join(Payment, Payment.id == Refund.payment_id), Refund, actor, tenant_id)
    if date_from:
        ref = ref.filter(Refund.created_at >= date_from)
    if date_to:
        ref = ref.filter(Refund.created_at <= date_to)
    refund_counts = {s: 0 for s in REFUND_STATUSES}
    refunded_base = tax_refunded = Decimal(0)
    requested_base = Decimal(0)
    for status, count, amount, tax in ref.group_by(Refund.status).all():
        refund_counts[status] = refund_counts.get(status, 0) + int(count)
        if status == "completed":
            refunded_base += to_decimal(amount or 0)
            tax_refunded += to_decimal(tax or 0)
        elif status in OPEN_REFUND_STATES:
            requested_base += to_decimal(amount or 0)
    open_refunds = sum(refund_counts[s] for s in OPEN_REFUND_STATES)
    open_query = _tenant_filter(db.query(func.count(Refund.id)), Refund, actor, tenant_id).filter(Refund.status.in_(OPEN_REFUND_STATES))
    overdue = open_query.filter(Refund.sla_due_at.isnot(None), Refund.sla_due_at < now).scalar() or 0
    escalated = open_query.filter(Refund.escalated_at.isnot(None)).scalar() or 0

    # ---- settlements
    st = _tenant_filter(db.query(MerchantSettlement.status, MerchantSettlement.currency, func.count(MerchantSettlement.id), func.sum(MerchantSettlement.net_amount)),
                        MerchantSettlement, actor, tenant_id)
    if date_from:
        st = st.filter(MerchantSettlement.created_at >= date_from)
    if date_to:
        st = st.filter(MerchantSettlement.created_at <= date_to)
    settlement_counts = {s: 0 for s in SETTLEMENT_STATUSES}
    by_currency: dict[str, dict] = {}
    for status, currency, count, net in st.group_by(MerchantSettlement.status, MerchantSettlement.currency).all():
        settlement_counts[status] = settlement_counts.get(status, 0) + int(count)
        bucket = by_currency.setdefault(currency, {"currency": currency, "pending_net": Decimal(0), "paid_net": Decimal(0)})
        if status == "pending":
            bucket["pending_net"] += to_decimal(net or 0)
        elif status == "paid":
            bucket["paid_net"] += to_decimal(net or 0)
    currencies = []
    for code, bucket in sorted(by_currency.items()):
        currencies.append({"currency": code, "pending_net": _money(db, bucket["pending_net"], code), "paid_net": _money(db, bucket["paid_net"], code)})

    open_mismatches = None
    if actor.role == "super_admin":
        open_mismatches = int(db.query(func.count(ReconciliationItem.id)).filter(ReconciliationItem.resolved.is_(False)).scalar() or 0)

    net_collected = collected - refunded_on_payments
    return {
        "base_currency": base,
        "period": {"date_from": date_from, "date_to": date_to},
        "payments": {
            "total": total, "paid": paid, "waiting": sum(by_status[s] for s in WAITING_STATES), "failed": by_status["failed"], "under_review": by_status["review"],
            "closed_unpaid": sum(by_status[s] for s in CLOSED_STATES), "by_status": by_status,
            "base_collected": _money(db, collected), "base_refunded": _money(db, refunded_on_payments), "base_net": _money(db, net_collected),
            "success_rate_percent": round(paid * 100 / total, 1) if total else None,
        },
        "refunds": {
            "total": sum(refund_counts.values()), "open": open_refunds, "completed": refund_counts["completed"], "failed": refund_counts["failed"], "rejected": refund_counts["rejected"],
            "overdue": int(overdue), "escalated": int(escalated), "by_status": refund_counts, "base_refunded": _money(db, refunded_base), "base_open_amount": _money(db, requested_base),
        },
        "tax": {"base_collected": _money(db, tax_collected), "base_refunded": _money(db, tax_refunded), "base_net": _money(db, tax_collected - tax_refunded)},
        "settlements": {
            "total": sum(settlement_counts.values()), "pending": settlement_counts["pending"], "paid": settlement_counts["paid"], "failed": settlement_counts["failed"],
            "on_hold": settlement_counts["on_hold"], "by_status": settlement_counts, "by_currency": currencies,
            "awaiting_settlement": {"payments": int(awaiting_count or 0), "base_amount": _money(db, awaiting_base)},
        },
        "needs_attention": {
            "payments_under_review": by_status["review"], "payments_needing_reconciliation": int(needs_reconciliation), "refunds_overdue": int(overdue),
            "refunds_failed": refund_counts["failed"], "settlements_failed": settlement_counts["failed"], "settlements_on_hold": settlement_counts["on_hold"],
            "open_reconciliation_mismatches": open_mismatches,
        },
    }


# ---------------------------------------------------------------- gateways (read-only; they are set in the server's environment)

def _mode(key: str, live_prefixes: tuple[str, ...], test_prefixes: tuple[str, ...]) -> str | None:
    key = (key or "").strip()
    if not key:
        return None
    if key.startswith(live_prefixes):
        return "live"
    if key.startswith(test_prefixes):
        return "test"
    return "unknown"


def gateway_status(db: Session, request_base: str | None = None) -> dict:
    """Which gateway takes payments, and whether it is set up. Never returns a key, a secret or any part of one."""
    base = (settings.PUBLIC_API_BASE_URL.strip() or (request_base or "")).rstrip("/")
    active = settings.PAYMENT_GATEWAY
    razorpay_key = settings.RAZORPAY_KEY_ID
    stripe_key = settings.STRIPE_SECRET_KEY
    providers = [
        {"provider": "razorpay", "label": "Razorpay", "active": active == "razorpay", "configured": bool(razorpay_key and settings.RAZORPAY_KEY_SECRET),
         "mode": _mode(razorpay_key, ("rzp_live_",), ("rzp_test_",)), "webhook_secret_set": bool(settings.RAZORPAY_WEBHOOK_SECRET), "publishable_key_set": None,
         "webhook_url": f"{base}/api/v1/payments/webhook/razorpay",
         "webhook_events": ["payment.authorized", "payment.captured", "payment.failed", "refund.processed", "refund.failed"]},
        {"provider": "stripe", "label": "Stripe", "active": active == "stripe", "configured": bool(stripe_key),
         "mode": _mode(stripe_key, ("sk_live_", "rk_live_"), ("sk_test_", "rk_test_")), "webhook_secret_set": bool(settings.STRIPE_WEBHOOK_SECRET),
         "publishable_key_set": bool(settings.STRIPE_PUBLISHABLE_KEY), "webhook_url": f"{base}/api/v1/payments/webhook/stripe",
         "webhook_events": ["payment_intent.succeeded", "payment_intent.payment_failed", "payment_intent.amount_capturable_updated", "payment_intent.canceled", "refund.updated"]},
        {"provider": "test", "label": "Test gateway (no money moves)", "active": active == "test", "configured": True, "mode": "test", "webhook_secret_set": True,
         "publishable_key_set": None, "webhook_url": f"{base}/api/v1/payments/webhook/test", "webhook_events": []},
    ]
    current = next(p for p in providers if p["active"])
    rates_loaded = db.query(func.count(ExchangeRate.id)).scalar() or 0
    rules = db.query(func.count(TaxRule.id)).filter(TaxRule.is_active.is_(True)).scalar() or 0
    warnings = []
    if active == "test":
        warnings.append("The test gateway is active: no real money moves." if not settings.is_production or settings.PAYMENT_ALLOW_TEST_GATEWAY else "The test gateway is active in production and will be refused.")
    elif not current["configured"]:
        warnings.append(f"{current['label']} is selected but its keys are not set on the server.")
    elif not current["webhook_secret_set"]:
        warnings.append(f"{current['label']} has no webhook secret, so its webhooks will be refused and payments depend on the scheduled check.")
    if active == "stripe" and not providers[1]["publishable_key_set"]:
        warnings.append("The Stripe publishable key is not set, so the app cannot open Stripe checkout.")
    if current["mode"] == "live" and not settings.is_production:
        warnings.append("A live key is set while the server is not in production mode.")
    if current["mode"] == "test" and settings.is_production and active != "test":
        warnings.append("A test key is set while the server is in production mode.")
    if not base:
        warnings.append("PUBLIC_API_BASE_URL is not set, so the webhook address shown is relative.")
    if not rates_loaded:
        warnings.append("No exchange rates are loaded. Payments in another currency are refused until rates are added.")
    if not rules:
        warnings.append("No tax rules are set up, so every payment is untaxed.")
    return {
        "editable": False, "edit_note": "The gateway and its keys are set in the server's environment, not through the API. Ask the platform team to change them.",
        "active_gateway": active, "active_mode": current["mode"], "providers": providers, "base_currency": currency_service.base_currency(),
        "exchange_rates": {"source_url": settings.EXCHANGE_RATE_API_URL, "max_age_hours": settings.EXCHANGE_RATE_MAX_AGE_HOURS, "rates_loaded": int(rates_loaded)},
        "tax": {"active_rules": int(rules)},
        "jobs": {"enabled": bool(settings.payment_jobs_enabled), "interval_seconds": settings.PAYMENT_JOBS_INTERVAL_SECONDS},
        "limits": {"max_attempts": settings.PAYMENT_MAX_ATTEMPTS, "stale_minutes": settings.PAYMENT_STALE_MINUTES, "gateway_timeout_seconds": settings.PAYMENT_GATEWAY_TIMEOUT_SECONDS,
                   "refund_sla_hours": settings.REFUND_SLA_HOURS},
        "risk": {"review_score": settings.PAYMENT_RISK_REVIEW_SCORE, "deny_score": settings.PAYMENT_RISK_DENY_SCORE, "high_amount_base": settings.PAYMENT_RISK_HIGH_AMOUNT_BASE,
                 "fraud_service_set": bool(settings.FRAUD_SERVICE_URL.strip())},
        "settlement_defaults": {"commission_percent": settings.PLATFORM_COMMISSION_PERCENT, "gateway_fee_percent": settings.GATEWAY_FEE_PERCENT},
        "warnings": warnings,
    }
