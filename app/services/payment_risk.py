"""The fraud / risk check that runs before a payment is sent to the gateway (FR-PAY-12).

A score from 0 to 100 is built from signals the platform already knows. At or above PAYMENT_RISK_REVIEW_SCORE the payment is
held for a Super Admin; at or above PAYMENT_RISK_DENY_SCORE it is refused. An outside scoring service can be added with
FRAUD_SERVICE_URL: its score is combined with the built-in one (the higher wins), and if it does not answer the built-in rules
decide alone. Step-up authentication (3-D Secure / OTP) is requested by the gateway itself.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

import requests
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.payment_model import Payment

logger = logging.getLogger(__name__)


@dataclass
class RiskResult:
    score: int = 0
    decision: str = "allow"                 # allow | review | deny
    reasons: list[str] = field(default_factory=list)


def assess(db: Session, *, user_id, email: str | None, base_amount: Decimal, charge_currency: str, billing: dict | None,
           now: datetime | None = None) -> RiskResult:
    now = now or datetime.utcnow()
    score, reasons = 0, []
    if base_amount >= Decimal(str(settings.PAYMENT_RISK_HIGH_AMOUNT_BASE)):
        score += 30
        reasons.append("high_amount")

    if user_id is not None:
        recent_failed = db.query(Payment).filter(
            Payment.customer_user_id == user_id, Payment.status == "failed", Payment.created_at >= now - timedelta(hours=1)).count()
        if recent_failed >= 5:
            score += 50
            reasons.append("many_failed_payments")
        elif recent_failed >= 3:
            score += 30
            reasons.append("repeated_failed_payments")
        recent_any = db.query(Payment).filter(Payment.customer_user_id == user_id, Payment.created_at >= now - timedelta(minutes=10)).count()
        if recent_any >= 5:
            score += 25
            reasons.append("payment_velocity")

    country = ((billing or {}).get("country") or "").upper()
    if country and charge_currency in ("EUR", "GBP", "USD") and country in ("KP", "IR", "SY", "CU"):
        score += 80                       # sanctioned places are never accepted
        reasons.append("restricted_country")

    external = _external_score(user_id, email, base_amount, charge_currency, billing)
    if external is not None:
        score = max(score, external[0])
        reasons += [f"fraud_service:{r}" for r in external[1]]

    score = min(score, 100)
    decision = "deny" if score >= settings.PAYMENT_RISK_DENY_SCORE else "review" if score >= settings.PAYMENT_RISK_REVIEW_SCORE else "allow"
    return RiskResult(score=score, decision=decision, reasons=reasons)


def _external_score(user_id, email, base_amount, currency, billing) -> tuple[int, list[str]] | None:
    if not settings.FRAUD_SERVICE_URL.strip():
        return None
    try:
        response = requests.post(settings.FRAUD_SERVICE_URL.strip(), timeout=3, json={
            "user_id": str(user_id) if user_id else None, "email": email, "amount_base": str(base_amount), "currency": currency,
            "billing_country": (billing or {}).get("country"),
        })
        response.raise_for_status()
        body = response.json()
        return max(0, min(100, int(body.get("score", 0)))), [str(r) for r in body.get("reasons", [])][:5]
    except Exception:
        logger.warning("the fraud service did not answer; using the built-in rules only")
        return None
