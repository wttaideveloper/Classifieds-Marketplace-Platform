"""Merchant settlement: collecting what a business has earned, taking the platform's share, and paying the rest out.

For each business, on its schedule (daily, weekly or monthly), every paid payment and completed refund that is not yet settled is
gathered into one settlement, in the business's payout currency:

    gross       what customers paid (tax included), converted to the payout currency; a refund counts as a negative item
    commission  the platform's share of the pre-tax amount (the business's own rate, else PLATFORM_COMMISSION_PERCENT); a refund
                gives its share back
    fee         the gateway's fee on each payment, actual when the gateway reported it, otherwise estimated (GATEWAY_FEE_PERCENT)
    fixed fee   an optional per-payment charge agreed with the business
    net         gross - commission - fee - fixed fee: what is paid out

Every item is kept (payment or refund, the amounts, the commission and fee on it), so the settlement can always be checked against
the transactions behind it (`verify`). A payment or refund belongs to at most one settlement. If the net is below the business's
minimum payout, or is not positive (refunds exceeded sales), nothing is settled and the items roll into the next run.
"""
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.payment_model import MerchantSettlement, Payment, Refund, SettlementConfig, SettlementItem
from app.services import currency_service, finance_audit, invoice_service, payment_notifications
from app.utils.money import quantize, to_decimal

logger = logging.getLogger(__name__)

PAID_STATES = ("succeeded", "partially_refunded", "refunded")
MANAGER_FIELDS = {"schedule", "weekday", "month_day", "currency", "min_payout", "payout_account"}
PLATFORM_FIELDS = {"commission_percent", "fixed_fee", "hold"}
CONFIG_FIELDS = ("schedule", "weekday", "month_day", "currency", "commission_percent", "fixed_fee", "min_payout", "hold", "payout_account")


def _now() -> datetime:
    return datetime.utcnow()


def _err(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


def _can_see(actor, tenant_id) -> bool:
    return actor.is_super_admin or (actor.role == "merchant" and tenant_id == actor.tenant_id)


# ---------------------------------------------------------------- configuration

def get_config(db: Session, tenant_id: UUID, *, create: bool = True, lock: bool = False) -> SettlementConfig | None:
    """`lock` holds the row until commit: two workers cannot settle the same business at the same time."""
    row = db.query(SettlementConfig).filter(SettlementConfig.tenant_id == tenant_id)
    row = (row.with_for_update() if lock else row).first()
    if row is None and create:
        row = SettlementConfig(tenant_id=tenant_id, currency=currency_service.base_currency(), schedule="weekly", weekday=0, month_day=1)
        db.add(row)
        db.flush()
    return row


def save_config(db: Session, actor, tenant_id: UUID, payload) -> SettlementConfig:
    """A business sets its schedule, payout currency, minimum and payout account; only the platform sets the commission, a fixed
    fee and a hold. Every change is audited."""
    data = payload.model_dump(exclude_unset=True)
    forbidden = set(data) & PLATFORM_FIELDS if not actor.is_super_admin else set()
    if forbidden:
        raise _err(403, f"Only the platform can change: {', '.join(sorted(forbidden))}")
    if "currency" in data and data["currency"]:
        currency_service.require_active_currency(db, data["currency"])
    row = get_config(db, tenant_id)
    before = finance_audit.snapshot(row, *CONFIG_FIELDS)
    for key, value in data.items():
        if key == "currency" and value:
            value = value.upper()
        if value is not None or key in ("commission_percent", "payout_account"):
            setattr(row, key, value)
    row.updated_by = actor.id
    finance_audit.record(db, actor, "settlement_config_saved", "settlement_config", tenant_id, before=before, after=finance_audit.snapshot(row, *CONFIG_FIELDS), tenant_id=tenant_id)
    db.commit()
    db.refresh(row)
    return row


def is_due(config: SettlementConfig, now: datetime) -> bool:
    last = config.last_run_at
    if config.hold:
        return False
    if last is not None and last.date() >= now.date():
        return False
    if config.schedule == "daily":
        return True
    if config.schedule == "weekly":
        return now.weekday() == config.weekday or last is None or (now - last) > timedelta(days=7)
    day = min(config.month_day, 28)
    return last is None or ((now.year, now.month) > (last.year, last.month) and now.day >= day)


# ---------------------------------------------------------------- running

class _Rates:
    """Charge currency -> payout currency, looked up once per currency for a run."""

    def __init__(self, db: Session, payout: str):
        self.db, self.payout, self.cache = db, payout, {}

    def rate(self, code: str) -> Decimal:
        if code not in self.cache:
            self.cache[code] = currency_service.rate_between(self.db, code, self.payout, enforce_age=False)[0]
        return self.cache[code]


def _collect(db: Session, tenant_id: UUID, cutoff: datetime) -> tuple[list[Payment], list[Refund]]:
    payments = db.query(Payment).filter(Payment.tenant_id == tenant_id, Payment.status.in_(PAID_STATES), Payment.settlement_id.is_(None),
                                        Payment.captured_at <= cutoff).order_by(Payment.captured_at, Payment.id).all()
    refunds = db.query(Refund).filter(Refund.tenant_id == tenant_id, Refund.status == "completed", Refund.settlement_id.is_(None),
                                      Refund.completed_at <= cutoff).order_by(Refund.completed_at, Refund.id).all()
    return payments, refunds


def build(db: Session, tenant_id: UUID, now: datetime) -> dict | None:
    """The settlement as it would be paid now, without saving anything. None when there is nothing to settle."""
    config = get_config(db, tenant_id)
    payments, refunds = _collect(db, tenant_id, now)
    if not payments and not refunds:
        return None
    payout = config.currency
    decimals = currency_service.decimals_for(db, payout)
    rates = _Rates(db, payout)
    pct = to_decimal(config.commission_percent) if config.commission_percent is not None else Decimal(str(settings.PLATFORM_COMMISSION_PERCENT))
    fixed = to_decimal(config.fixed_fee)
    fee_pct = Decimal(str(settings.GATEWAY_FEE_PERCENT))
    items, totals = [], {"gross": Decimal(0), "refunds": Decimal(0), "commission": Decimal(0), "fee": Decimal(0), "fixed": Decimal(0), "tax": Decimal(0)}

    for payment in payments:
        rate = rates.rate(payment.charge_currency)
        gross = quantize(to_decimal(payment.charge_total) * rate, decimals)
        pre_tax = quantize(to_decimal(payment.charge_subtotal) * rate, decimals)
        commission = quantize(pre_tax * pct / 100, decimals)
        fee = quantize(to_decimal(payment.gateway_fee) * rate, decimals) if payment.gateway_fee is not None else quantize(gross * fee_pct / 100, decimals)
        item = {"kind": "payment", "payment_id": payment.id, "refund_id": None, "charge_currency": payment.charge_currency, "charge_amount": to_decimal(payment.charge_total),
                "gross": gross, "commission": commission, "fee": fee, "fixed_fee": fixed, "net": gross - commission - fee - fixed}
        items.append(item)
        totals["gross"] += gross
        totals["commission"] += commission
        totals["fee"] += fee
        totals["fixed"] += fixed
        totals["tax"] += quantize(to_decimal(payment.charge_tax) * rate, decimals)
    for refund in refunds:
        rate = rates.rate(refund.currency)
        gross = quantize(to_decimal(refund.amount) * rate, decimals)
        pre_tax = quantize((to_decimal(refund.amount) - to_decimal(refund.tax_refunded)) * rate, decimals)
        commission = quantize(pre_tax * pct / 100, decimals)
        items.append({"kind": "refund", "payment_id": refund.payment_id, "refund_id": refund.id, "charge_currency": refund.currency, "charge_amount": to_decimal(refund.amount),
                      "gross": -gross, "commission": -commission, "fee": Decimal(0), "fixed_fee": Decimal(0), "net": -(gross - commission)})
        totals["refunds"] += gross
        totals["commission"] -= commission
        totals["tax"] -= quantize(to_decimal(refund.tax_refunded) * rate, decimals)

    net = totals["gross"] - totals["refunds"] - totals["commission"] - totals["fee"] - totals["fixed"]
    base = currency_service.base_currency()
    base_to_payout = currency_service.rate_between(db, base, payout, enforce_age=False)[0]
    return {"tenant_id": tenant_id, "currency": payout, "base_currency": base, "exchange_rate": base_to_payout, "items": items, "payments": payments, "refunds": refunds,
            "gross_amount": totals["gross"], "refund_amount": totals["refunds"], "commission_amount": totals["commission"], "fee_amount": totals["fee"],
            "fixed_fee_amount": totals["fixed"], "tax_collected": totals["tax"], "net_amount": net, "min_payout": to_decimal(config.min_payout),
            "period_start": min([p.captured_at for p in payments] + [r.completed_at for r in refunds]), "period_end": now}


def run_for_tenant(db: Session, tenant_id: UUID, now: datetime | None = None, *, actor=None, force: bool = False) -> MerchantSettlement | None:
    now = now or _now()
    config = get_config(db, tenant_id, lock=True)
    if config.hold and not force:
        return None
    plan = build(db, tenant_id, now)
    config.last_run_at = now
    if plan is None:
        db.commit()
        return None
    if not force and (plan["net_amount"] <= 0 or plan["net_amount"] < plan["min_payout"]):
        db.commit()                       # not enough yet: the items stay unsettled and roll into the next run
        return None
    _, number = invoice_service.next_number(db, None, "SET", now.year)
    settlement = MerchantSettlement(
        reference=number, tenant_id=tenant_id, period_start=plan["period_start"], period_end=plan["period_end"], currency=plan["currency"],
        base_currency=plan["base_currency"], exchange_rate=plan["exchange_rate"], gross_amount=plan["gross_amount"], refund_amount=plan["refund_amount"],
        commission_amount=plan["commission_amount"], fee_amount=plan["fee_amount"], fixed_fee_amount=plan["fixed_fee_amount"], tax_collected=plan["tax_collected"],
        net_amount=plan["net_amount"], items_count=len(plan["items"]), status="pending", created_by=getattr(actor, "id", None) or "system",
    )
    db.add(settlement)
    db.flush()
    for item in plan["items"]:
        db.add(SettlementItem(settlement_id=settlement.id, **item))
    for payment in plan["payments"]:
        payment.settlement_id = settlement.id
    for refund in plan["refunds"]:
        refund.settlement_id = settlement.id
    finance_audit.record(db, actor, "settlement_created", "settlement", settlement.id, after={"reference": number, "net": settlement.net_amount, "items": settlement.items_count},
                         tenant_id=tenant_id)
    db.commit()
    db.refresh(settlement)
    return settlement


def run_due(db: Session, now: datetime | None = None) -> dict:
    """The scheduled job: settles every business whose turn it is. A business with unsettled money and no schedule yet gets the default."""
    now = now or _now()
    tenants = {t for (t,) in db.query(Payment.tenant_id).filter(Payment.tenant_id.isnot(None), Payment.status.in_(PAID_STATES), Payment.settlement_id.is_(None)).distinct()}
    tenants |= {t for (t,) in db.query(Refund.tenant_id).filter(Refund.tenant_id.isnot(None), Refund.status == "completed", Refund.settlement_id.is_(None)).distinct()}
    summary = {"businesses": len(tenants), "created": 0, "skipped": 0, "failed": 0}
    for tenant_id in sorted(tenants, key=str):
        try:
            config = get_config(db, tenant_id)
            if not is_due(config, now):
                summary["skipped"] += 1
                continue
            if run_for_tenant(db, tenant_id, now) is not None:
                summary["created"] += 1
            else:
                summary["skipped"] += 1
        except Exception:
            db.rollback()
            logger.exception("settlement failed for %s", tenant_id)
            summary["failed"] += 1
    return summary


# ---------------------------------------------------------------- after the run

def get_settlement(db: Session, actor, settlement_id: UUID) -> MerchantSettlement:
    row = db.get(MerchantSettlement, settlement_id)
    if row is None or not _can_see(actor, row.tenant_id):
        raise _err(404, "Settlement not found")
    return row


def items_of(db: Session, settlement: MerchantSettlement) -> list[SettlementItem]:
    return db.query(SettlementItem).filter(SettlementItem.settlement_id == settlement.id).order_by(SettlementItem.created_at, SettlementItem.id).all()


def list_settlements(db: Session, actor, *, status: str | None = None, tenant_id: UUID | None = None, date_from: datetime | None = None,
                     date_to: datetime | None = None, page: int = 1, page_size: int = 20) -> tuple[list[MerchantSettlement], int]:
    query = db.query(MerchantSettlement)
    if actor.role == "merchant":
        query = query.filter(MerchantSettlement.tenant_id == actor.tenant_id)
    elif actor.role != "super_admin":
        raise _err(403, "Settlements are for businesses and the platform")
    elif tenant_id:
        query = query.filter(MerchantSettlement.tenant_id == tenant_id)
    if status:
        query = query.filter(MerchantSettlement.status == status)
    if date_from:
        query = query.filter(MerchantSettlement.created_at >= date_from)
    if date_to:
        query = query.filter(MerchantSettlement.created_at <= date_to)
    total = query.count()
    return query.order_by(MerchantSettlement.created_at.desc(), MerchantSettlement.id).offset((page - 1) * page_size).limit(page_size).all(), total


def mark_paid(db: Session, actor, settlement_id: UUID, payout_reference: str, *, access_token: str | None = None) -> MerchantSettlement:
    """Finance records that the payout was made (FR-SET-01). The money moves through the bank or payout service, not here."""
    row = get_settlement(db, actor, settlement_id)
    if not actor.is_super_admin:
        raise _err(403, "Only the platform's finance team can mark a settlement paid")
    if row.status != "pending":
        raise _err(409, f"This settlement is {row.status}")
    row.status, row.paid_at, row.payout_reference, row.failure_reason = "paid", _now(), payout_reference.strip()[:120], None
    finance_audit.record(db, actor, "settlement_paid", "settlement", row.id, after={"reference": row.reference, "payout_reference": row.payout_reference, "net": row.net_amount},
                         tenant_id=row.tenant_id)
    db.commit()
    db.refresh(row)
    payment_notifications.notify_settlement_completed(row.id, access_token=access_token)
    return row


def mark_failed(db: Session, actor, settlement_id: UUID, reason: str) -> MerchantSettlement:
    row = get_settlement(db, actor, settlement_id)
    if not actor.is_super_admin:
        raise _err(403, "Only the platform's finance team can do this")
    if row.status != "pending":
        raise _err(409, f"This settlement is {row.status}")
    row.status, row.failure_reason = "failed", reason.strip()[:255]
    finance_audit.record(db, actor, "settlement_failed", "settlement", row.id, after={"reason": row.failure_reason}, tenant_id=row.tenant_id, reason=reason)
    db.commit()
    db.refresh(row)
    return row


def cancel(db: Session, actor, settlement_id: UUID, reason: str) -> MerchantSettlement:
    """Cancels a settlement that is not paid; its payments and refunds go back to being unsettled and join the next one."""
    row = get_settlement(db, actor, settlement_id)
    if not actor.is_super_admin:
        raise _err(403, "Only the platform's finance team can cancel a settlement")
    if row.status not in ("pending", "failed"):
        raise _err(409, f"A settlement that is {row.status} cannot be cancelled")
    db.query(Payment).filter(Payment.settlement_id == row.id).update({Payment.settlement_id: None}, synchronize_session=False)
    db.query(Refund).filter(Refund.settlement_id == row.id).update({Refund.settlement_id: None}, synchronize_session=False)
    row.status = "cancelled"
    finance_audit.record(db, actor, "settlement_cancelled", "settlement", row.id, after={"reference": row.reference}, tenant_id=row.tenant_id, reason=reason)
    db.commit()
    db.refresh(row)
    return row


def verify(db: Session, settlement: MerchantSettlement) -> dict:
    """Checks the settlement against the payments and refunds behind it (the 1:1 reconciliation). Every difference is listed."""
    problems = []
    items = items_of(db, settlement)
    payments = {p.id: p for p in db.query(Payment).filter(Payment.settlement_id == settlement.id).all()}
    refunds = {r.id: r for r in db.query(Refund).filter(Refund.settlement_id == settlement.id).all()}
    item_payments = {i.payment_id for i in items if i.kind == "payment"}
    item_refunds = {i.refund_id for i in items if i.kind == "refund"}
    for missing in sorted(set(payments) - item_payments, key=str):
        problems.append({"issue": "payment_without_item", "payment_id": str(missing)})
    for missing in sorted(item_payments - set(payments), key=str):
        problems.append({"issue": "item_without_payment", "payment_id": str(missing)})
    for missing in sorted(set(refunds) - item_refunds, key=str):
        problems.append({"issue": "refund_without_item", "refund_id": str(missing)})
    for missing in sorted(item_refunds - set(refunds), key=str):
        problems.append({"issue": "item_without_refund", "refund_id": str(missing)})
    for item in items:
        source = payments.get(item.payment_id) if item.kind == "payment" else refunds.get(item.refund_id)
        if source is not None:
            expected = to_decimal(source.charge_total if item.kind == "payment" else source.amount)
            if expected != to_decimal(item.charge_amount):
                problems.append({"issue": "amount_changed", "kind": item.kind, "expected": format(expected, "f"), "item": format(to_decimal(item.charge_amount), "f")})
        if to_decimal(item.gross) - to_decimal(item.commission) - to_decimal(item.fee) - to_decimal(item.fixed_fee) != to_decimal(item.net) and item.kind == "payment":
            problems.append({"issue": "item_arithmetic", "payment_id": str(item.payment_id)})
    sums = {
        "gross_amount": sum((to_decimal(i.gross) for i in items if i.kind == "payment"), Decimal(0)),
        "refund_amount": -sum((to_decimal(i.gross) for i in items if i.kind == "refund"), Decimal(0)),
        "commission_amount": sum((to_decimal(i.commission) for i in items), Decimal(0)),
        "fee_amount": sum((to_decimal(i.fee) for i in items), Decimal(0)),
        "fixed_fee_amount": sum((to_decimal(i.fixed_fee) for i in items), Decimal(0)),
        "net_amount": sum((to_decimal(i.net) for i in items), Decimal(0)),
    }
    for field_name, total in sums.items():
        if to_decimal(getattr(settlement, field_name)) != total:
            problems.append({"issue": "total_mismatch", "field": field_name, "settlement": format(to_decimal(getattr(settlement, field_name)), "f"), "items": format(total, "f")})
    if settlement.items_count != len(items):
        problems.append({"issue": "item_count", "settlement": settlement.items_count, "items": len(items)})
    return {"settlement_id": str(settlement.id), "reference": settlement.reference, "reconciled": not problems, "items_checked": len(items),
            "payments": len(payments), "refunds": len(refunds), "problems": problems}
