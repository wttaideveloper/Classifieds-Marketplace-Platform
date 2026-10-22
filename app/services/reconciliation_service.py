"""Reconciliation: the gateway's own report compared with our payment and refund records.

The finance team uploads the gateway's settlement / payment report rows. Each row is matched to a payment (or refund) by the
gateway's id and compared on currency, amount, status and fee. Differences are saved as items, to be looked at and resolved:

    missing_internal   the gateway has money we have no record of
    missing_gateway    we say a payment was paid but the report in this period does not have it
    amount_mismatch / currency_mismatch / status_mismatch / fee_mismatch
    duplicate          the same gateway id appears twice in the report
"""
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.payment_model import Payment, ReconciliationItem, ReconciliationRun, Refund
from app.services import finance_audit
from app.utils.money import to_decimal

PAID_STATES = ("succeeded", "partially_refunded", "refunded")
GATEWAY_PAID = {"captured", "succeeded", "paid", "settled", "success"}
GATEWAY_FAILED = {"failed", "declined", "cancelled", "canceled"}
FEE_TOLERANCE = Decimal("0.02")


def _now() -> datetime:
    return datetime.utcnow()


def reconcile_report(db: Session, actor, provider: str, rows: list[dict], period_start: datetime | None, period_end: datetime | None) -> ReconciliationRun:
    run = ReconciliationRun(kind="gateway", provider=provider, period_start=period_start, period_end=period_end, total_rows=len(rows), created_by=getattr(actor, "id", None))
    db.add(run)
    db.flush()
    seen: set[tuple[str, str]] = set()
    matched_payments: set = set()
    items: list[ReconciliationItem] = []

    def flag(issue: str, row: dict | None = None, payment: Payment | None = None, **extra) -> None:
        items.append(ReconciliationItem(
            run_id=run.id, issue=issue, gateway_ref=(row or {}).get("gateway_ref") or (payment.gateway_payment_id if payment else None),
            payment_id=payment.id if payment else None, gateway_amount=to_decimal((row or {}).get("amount"), Decimal(0)) if row and row.get("amount") is not None else None,
            internal_amount=to_decimal(payment.charge_total) if payment else None, currency=((row or {}).get("currency") or (payment.charge_currency if payment else None) or "")[:3].upper() or None,
            gateway_status=(row or {}).get("status"), internal_status=payment.status if payment else None, detail=finance_audit.jsonable(extra) if extra else None,
        ))

    for row in rows:
        kind = (row.get("type") or "payment").lower()
        ref = str(row.get("gateway_ref") or "").strip()
        if not ref:
            flag("missing_internal", row, reason="the row has no gateway_ref")
            continue
        key = (kind, ref)
        if key in seen:
            flag("duplicate", row)
            continue
        seen.add(key)
        if kind == "refund":
            refund = db.query(Refund).filter(Refund.gateway_refund_id == ref).first()
            if refund is None:
                flag("missing_internal", row, kind="refund")
            elif to_decimal(row.get("amount"), Decimal(-1)) != to_decimal(refund.amount) or (row.get("currency") or refund.currency).upper() != refund.currency:
                flag("amount_mismatch", row, db.get(Payment, refund.payment_id), kind="refund", internal_refund=refund.amount)
            continue
        payment = db.query(Payment).filter((Payment.gateway_payment_id == ref) | (Payment.gateway_order_id == ref)).first()
        if payment is None:
            flag("missing_internal", row)
            continue
        matched_payments.add(payment.id)
        status = str(row.get("status") or "").lower()
        if (row.get("currency") or payment.charge_currency).upper() != payment.charge_currency:
            flag("currency_mismatch", row, payment)
        elif row.get("amount") is not None and to_decimal(row["amount"], Decimal(-1)) != to_decimal(payment.charge_total):
            flag("amount_mismatch", row, payment)
        elif (status in GATEWAY_PAID and payment.status not in PAID_STATES) or (status in GATEWAY_FAILED and payment.status in PAID_STATES):
            flag("status_mismatch", row, payment)
        elif row.get("fee") is not None and payment.gateway_fee is not None and abs(to_decimal(row["fee"], Decimal(0)) - to_decimal(payment.gateway_fee)) > FEE_TOLERANCE:
            flag("fee_mismatch", row, payment, internal_fee=payment.gateway_fee)

    if period_start and period_end:
        ours = db.query(Payment).filter(Payment.provider == provider, Payment.status.in_(PAID_STATES), Payment.captured_at >= period_start, Payment.captured_at <= period_end).all()
        for payment in ours:
            if payment.id not in matched_payments:
                flag("missing_gateway", None, payment)

    for item in items:
        db.add(item)
    run.mismatched = len(items)
    run.matched = max(0, len(rows) - len({i.gateway_ref for i in items if i.issue != "missing_gateway"}))
    finance_audit.record(db, actor, "reconciliation_run", "reconciliation", run.id, after={"rows": len(rows), "mismatched": run.mismatched, "provider": provider})
    db.commit()
    db.refresh(run)
    return run


def list_runs(db: Session, page: int = 1, page_size: int = 20) -> tuple[list[ReconciliationRun], int]:
    query = db.query(ReconciliationRun)
    return query.order_by(ReconciliationRun.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all(), query.count()


def get_run(db: Session, run_id) -> ReconciliationRun:
    run = db.get(ReconciliationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Reconciliation run not found")
    return run


def run_items(db: Session, run_id, *, issue: str | None = None, resolved: bool | None = None) -> list[ReconciliationItem]:
    query = db.query(ReconciliationItem).filter(ReconciliationItem.run_id == run_id)
    if issue:
        query = query.filter(ReconciliationItem.issue == issue)
    if resolved is not None:
        query = query.filter(ReconciliationItem.resolved.is_(resolved))
    return query.order_by(ReconciliationItem.created_at, ReconciliationItem.id).all()


def resolve_item(db: Session, actor, item_id, note: str) -> ReconciliationItem:
    item = db.get(ReconciliationItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    if item.resolved:
        raise HTTPException(status_code=409, detail="This item is already resolved")
    item.resolved, item.resolved_by, item.resolved_at, item.note = True, actor.id, _now(), note.strip()[:255]
    finance_audit.record(db, actor, "reconciliation_item_resolved", "reconciliation_item", item.id, after={"issue": item.issue, "note": item.note}, reason=note)
    db.commit()
    db.refresh(item)
    return item
