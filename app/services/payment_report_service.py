"""Finance reports: payments, refunds, settlements, currency, exchange rates and reconciliation.

A business sees its own figures, the platform sees everything (and can narrow to one business with tenant_id). Every report gives
rows and totals; the totals are also given in the base currency so different currencies can be added up. Amounts are strings in
JSON and exact in CSV.
"""
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.payment_model import Invoice, MerchantSettlement, Payment, ReconciliationItem, ReconciliationRun, Refund
from app.services import currency_service
from app.utils.money import quantize, to_decimal

PAID_STATES = ("succeeded", "partially_refunded", "refunded")


def _require_finance(actor) -> None:
    if actor.role not in ("merchant", "super_admin"):
        raise HTTPException(status_code=403, detail="Reports are for businesses and the platform")


def _tenant_filter(query, model, actor, tenant_id: UUID | None):
    if actor.role == "merchant":
        return query.filter(model.tenant_id == actor.tenant_id)
    return query.filter(model.tenant_id == tenant_id) if tenant_id else query


def _q(db: Session, value, code: str):
    """An amount with the decimals of its currency (2 for INR, 0 for JPY, 3 for KWD)."""
    return None if value is None else quantize(to_decimal(value), currency_service.decimals_for(db, code))


def _base_total(db: Session, amount: Decimal) -> Decimal:
    return quantize(amount, currency_service.decimals_for(db, currency_service.base_currency()))


def payment_report(db: Session, actor, *, tenant_id=None, date_from=None, date_to=None, status=None, currency=None, page=1, page_size=100) -> dict:
    _require_finance(actor)
    query = _tenant_filter(db.query(Payment), Payment, actor, tenant_id)
    if status:
        query = query.filter(Payment.status == status)
    if currency:
        query = query.filter(Payment.charge_currency == currency.upper())
    if date_from:
        query = query.filter(Payment.created_at >= date_from)
    if date_to:
        query = query.filter(Payment.created_at <= date_to)
    everything = query.order_by(Payment.created_at.desc()).all()
    invoices = {i.payment_id: i.number for i in db.query(Invoice).filter(Invoice.payment_id.in_([p.id for p in everything] or [None]), Invoice.doc_type == "invoice").all()}
    rows = [{
        "payment_id": str(p.id), "created_at": p.created_at, "captured_at": p.captured_at, "status": p.status, "tenant_id": str(p.tenant_id) if p.tenant_id else None,
        "customer_email": p.customer_email, "purchase_type": p.purchase_type, "purchase_id": p.purchase_id, "attempt_no": p.attempt_no, "provider": p.provider,
        "item_currency": p.item_currency, "item_total": _q(db, p.total_amount, p.item_currency), "charge_currency": p.charge_currency,
        "charge_subtotal": _q(db, p.charge_subtotal, p.charge_currency), "charge_tax": _q(db, p.charge_tax, p.charge_currency),
        "charge_total": _q(db, p.charge_total, p.charge_currency), "exchange_rate": to_decimal(p.exchange_rate), "base_currency": p.base_currency,
        "base_total": _q(db, p.base_total, p.base_currency), "gateway_fee": _q(db, p.gateway_fee, p.charge_currency),
        "refunded_amount": _q(db, p.refunded_amount, p.charge_currency),
        "method": f"{p.method_brand or ''} {p.method_last4 or ''}".strip() or None, "failure_code": p.failure_code, "invoice_number": invoices.get(p.id),
    } for p in everything]
    paid = [p for p in everything if p.status in PAID_STATES]
    totals = {
        "payments": len(everything), "paid": len(paid), "failed": sum(1 for p in everything if p.status == "failed"),
        "base_collected": _base_total(db, sum((to_decimal(p.base_total) for p in paid), Decimal(0))),
        "base_refunded": _base_total(db, sum((to_decimal(p.refunded_amount) * to_decimal(p.base_rate) for p in paid), Decimal(0))),
        "base_tax": _base_total(db, sum((to_decimal(p.charge_tax) * to_decimal(p.base_rate) for p in paid), Decimal(0))),
    }
    start = (page - 1) * page_size
    return {"base_currency": currency_service.base_currency(), "rows": rows[start:start + page_size], "total_rows": len(rows), "page": page, "page_size": page_size, "totals": totals}


def refund_report(db: Session, actor, *, tenant_id=None, date_from=None, date_to=None, status=None, page=1, page_size=100) -> dict:
    _require_finance(actor)
    query = _tenant_filter(db.query(Refund, Payment).join(Payment, Payment.id == Refund.payment_id), Refund, actor, tenant_id)
    if status:
        query = query.filter(Refund.status == status)
    if date_from:
        query = query.filter(Refund.created_at >= date_from)
    if date_to:
        query = query.filter(Refund.created_at <= date_to)
    pairs = query.order_by(Refund.created_at.desc()).all()
    rows = [{
        "refund_id": str(r.id), "payment_id": str(r.payment_id), "created_at": r.created_at, "approved_at": r.approved_at, "completed_at": r.completed_at, "status": r.status,
        "refund_type": r.refund_type, "destination": r.destination, "currency": r.currency, "amount": _q(db, r.amount, r.currency), "tax_refunded": _q(db, r.tax_refunded, r.currency),
        "base_amount": quantize(to_decimal(r.amount) * to_decimal(p.base_rate), currency_service.decimals_for(db, p.base_currency)), "reason": r.reason,
        "requested_by_role": r.requested_by_role, "sla_due_at": r.sla_due_at, "escalated": r.escalated_at is not None,
        "turnaround_hours": round((r.completed_at - r.created_at).total_seconds() / 3600, 1) if r.completed_at else None,
    } for r, p in pairs]
    done = [row for row in rows if row["status"] == "completed"]
    totals = {"refunds": len(rows), "completed": len(done), "open": sum(1 for r in rows if r["status"] in ("requested", "approved", "processing")),
              "escalated": sum(1 for r in rows if r["escalated"]), "base_refunded": _base_total(db, sum((r["base_amount"] for r in done), Decimal(0)))}
    start = (page - 1) * page_size
    return {"base_currency": currency_service.base_currency(), "rows": rows[start:start + page_size], "total_rows": len(rows), "page": page, "page_size": page_size, "totals": totals}


def settlement_report(db: Session, actor, *, tenant_id=None, date_from=None, date_to=None, status=None, page=1, page_size=100) -> dict:
    _require_finance(actor)
    query = _tenant_filter(db.query(MerchantSettlement), MerchantSettlement, actor, tenant_id)
    if status:
        query = query.filter(MerchantSettlement.status == status)
    if date_from:
        query = query.filter(MerchantSettlement.created_at >= date_from)
    if date_to:
        query = query.filter(MerchantSettlement.created_at <= date_to)
    settlements = query.order_by(MerchantSettlement.created_at.desc()).all()
    rows = [{
        "settlement_id": str(s.id), "reference": s.reference, "tenant_id": str(s.tenant_id), "status": s.status, "currency": s.currency, "period_start": s.period_start,
        "period_end": s.period_end, "gross_amount": _q(db, s.gross_amount, s.currency), "refund_amount": _q(db, s.refund_amount, s.currency),
        "commission_amount": _q(db, s.commission_amount, s.currency), "fee_amount": _q(db, s.fee_amount, s.currency), "fixed_fee_amount": _q(db, s.fixed_fee_amount, s.currency),
        "tax_collected": _q(db, s.tax_collected, s.currency), "net_amount": _q(db, s.net_amount, s.currency), "items_count": s.items_count, "paid_at": s.paid_at,
        "payout_reference": s.payout_reference,
    } for s in settlements]
    by_currency: dict[str, dict] = {}
    for row in rows:
        bucket = by_currency.setdefault(row["currency"], {"settlements": 0, "net": Decimal(0), "paid": Decimal(0), "pending": Decimal(0)})
        bucket["settlements"] += 1
        bucket["net"] += row["net_amount"]
        if row["status"] == "paid":
            bucket["paid"] += row["net_amount"]
        elif row["status"] == "pending":
            bucket["pending"] += row["net_amount"]
    start = (page - 1) * page_size
    return {"rows": rows[start:start + page_size], "total_rows": len(rows), "page": page, "page_size": page_size, "totals": {"settlements": len(rows), "by_currency": by_currency}}


def currency_report(db: Session, actor, *, tenant_id=None, date_from=None, date_to=None) -> dict:
    """Paid payments grouped by the currency the customers paid in."""
    _require_finance(actor)
    query = _tenant_filter(db.query(Payment).filter(Payment.status.in_(PAID_STATES)), Payment, actor, tenant_id)
    if date_from:
        query = query.filter(Payment.created_at >= date_from)
    if date_to:
        query = query.filter(Payment.created_at <= date_to)
    groups: dict[str, dict] = {}
    for p in query.all():
        g = groups.setdefault(p.charge_currency, {"currency": p.charge_currency, "payments": 0, "charge_total": Decimal(0), "tax": Decimal(0), "refunded": Decimal(0),
                                                  "base_total": Decimal(0), "rate_min": None, "rate_max": None})
        g["payments"] += 1
        g["charge_total"] += to_decimal(p.charge_total)
        g["tax"] += to_decimal(p.charge_tax)
        g["refunded"] += to_decimal(p.refunded_amount)
        g["base_total"] += to_decimal(p.base_total)
        rate = to_decimal(p.base_rate)
        g["rate_min"] = rate if g["rate_min"] is None else min(g["rate_min"], rate)
        g["rate_max"] = rate if g["rate_max"] is None else max(g["rate_max"], rate)
    rows = sorted(groups.values(), key=lambda g: g["currency"])
    decimals = currency_service.decimals_for(db, currency_service.base_currency())
    for g in rows:
        g["base_total"] = quantize(g["base_total"], decimals)
        for name in ("charge_total", "tax", "refunded"):
            g[name] = _q(db, g[name], g["currency"])
    return {"base_currency": currency_service.base_currency(), "rows": rows,
            "totals": {"payments": sum(g["payments"] for g in rows), "base_total": quantize(sum((g["base_total"] for g in rows), Decimal(0)), decimals)}}


def exchange_rate_report(db: Session, actor, *, quote=None, date_from=None, date_to=None) -> dict:
    """The rate history with its low, high and average per currency in the window."""
    if actor.role != "super_admin":
        raise HTTPException(status_code=403, detail="The exchange rate report is for the platform")
    rows, total = currency_service.rate_history(db, quote=quote, date_from=date_from, date_to=date_to, page=1, page_size=5000)
    stats: dict[str, dict] = {}
    for r in rows:
        s = stats.setdefault(r.quote_currency, {"currency": r.quote_currency, "changes": 0, "low": None, "high": None, "sum": Decimal(0)})
        rate = to_decimal(r.rate)
        s["changes"] += 1
        s["sum"] += rate
        s["low"] = rate if s["low"] is None else min(s["low"], rate)
        s["high"] = rate if s["high"] is None else max(s["high"], rate)
    summary = [{"currency": s["currency"], "changes": s["changes"], "low": s["low"], "high": s["high"], "average": (s["sum"] / s["changes"]).quantize(Decimal("0.0000000001"))}
               for s in sorted(stats.values(), key=lambda s: s["currency"])]
    history = [{"quote_currency": r.quote_currency, "base_currency": r.base_currency, "rate": to_decimal(r.rate), "source": r.source, "pinned": r.pinned,
                "created_by": r.created_by, "created_at": r.created_at, "note": r.note} for r in rows]
    return {"base_currency": currency_service.base_currency(), "summary": summary, "rows": history, "total_rows": total}


def reconciliation_report(db: Session, actor, *, date_from=None, date_to=None) -> dict:
    if actor.role != "super_admin":
        raise HTTPException(status_code=403, detail="The reconciliation report is for the platform")
    runs = db.query(ReconciliationRun)
    if date_from:
        runs = runs.filter(ReconciliationRun.created_at >= date_from)
    if date_to:
        runs = runs.filter(ReconciliationRun.created_at <= date_to)
    runs = runs.order_by(ReconciliationRun.created_at.desc()).all()
    ids = [r.id for r in runs]
    items = db.query(ReconciliationItem).filter(ReconciliationItem.run_id.in_(ids or [None])).all()
    by_issue: dict[str, dict] = {}
    for i in items:
        b = by_issue.setdefault(i.issue, {"issue": i.issue, "items": 0, "open": 0})
        b["items"] += 1
        b["open"] += 0 if i.resolved else 1
    rows = [{"run_id": str(r.id), "provider": r.provider, "created_at": r.created_at, "period_start": r.period_start, "period_end": r.period_end,
             "rows": r.total_rows, "matched": r.matched, "mismatched": r.mismatched} for r in runs]
    return {"rows": rows, "by_issue": sorted(by_issue.values(), key=lambda b: b["issue"]),
            "totals": {"runs": len(runs), "items": len(items), "open": sum(1 for i in items if not i.resolved)}}
