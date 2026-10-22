from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from sqlalchemy.orm import Session

from app.core.payment_access import PayActor, get_pay_actor, require_merchant_or_admin, require_super_admin
from app.db.database import get_db
from app.schemas.settlement_schema import (
    PayoutBody, ReasonBody, SettlementConfigBody, SettlementConfigResponse, SettlementDetailResponse, SettlementListResponse,
    SettlementResponse, SettlementRunRequest,
)
from app.services import payment_export, payment_view
from app.services import settlement_service as svc

router = APIRouter(tags=["Settlement"])


def _tenant(actor: PayActor, tenant_id: UUID | None) -> UUID:
    if actor.role == "merchant":
        if tenant_id not in (None, actor.tenant_id):
            raise HTTPException(status_code=403, detail="You can only use your own business's settlements")
        return actor.tenant_id
    if tenant_id is None:
        raise HTTPException(status_code=422, detail="tenant_id is required")
    return tenant_id


@router.get("/config", response_model=SettlementConfigResponse, summary="Settlement schedule and payout currency of a business",
            description="FR-SET-04, FR-SET-05. When and in which currency a business is paid out, its minimum payout and its commission.")
def get_config(tenant_id: UUID | None = Query(None, description="Super Admin only"), db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    config = svc.get_config(db, _tenant(actor, tenant_id))
    db.commit()
    return payment_view.config_response(config, db)


@router.put("/config", response_model=SettlementConfigResponse, summary="Change the settlement schedule or payout currency",
            description="A business sets `schedule` (daily, weekly, monthly), `weekday`, `month_day`, `currency`, `min_payout` and a masked `payout_account`. Only the platform can change "
                        "`commission_percent`, `fixed_fee` and `hold`. Every change is audited.")
def save_config(payload: SettlementConfigBody = Body(...), tenant_id: UUID | None = Query(None, description="Super Admin only"), db: Session = Depends(get_db),
                actor: PayActor = Depends(require_merchant_or_admin)):
    return payment_view.config_response(svc.save_config(db, actor, _tenant(actor, tenant_id), payload), db)


@router.post("/run", summary="Run settlements now (Super Admin)",
             description="Settles every business that is due, or one business with `tenant_id` (`force` ignores the schedule and the minimum; `dry_run` shows what would be settled "
                         "without saving). The same job runs on a schedule.")
def run_settlements(payload: SettlementRunRequest = Body(default_factory=SettlementRunRequest), db: Session = Depends(get_db), actor: PayActor = Depends(require_super_admin)):
    now = datetime.utcnow()
    if payload.tenant_id is None:
        if payload.force or payload.dry_run:
            raise HTTPException(status_code=422, detail="force and dry_run need a tenant_id")
        return svc.run_due(db, now)
    if payload.dry_run:
        plan = svc.build(db, payload.tenant_id, now)
        db.rollback()
        if plan is None:
            return {"settlement": None, "message": "Nothing to settle"}
        shown = {"currency": plan["currency"], "base_currency": plan["base_currency"], "exchange_rate": format(plan["exchange_rate"], "f"),
                 "period_start": plan["period_start"], "period_end": plan["period_end"], "items_count": len(plan["items"]),
                 "payments": len(plan["payments"]), "refunds": len(plan["refunds"]), "min_payout": plan["min_payout"]}
        for name in ("gross_amount", "refund_amount", "commission_amount", "fee_amount", "fixed_fee_amount", "tax_collected", "net_amount", "min_payout"):
            shown[name] = str(payment_view._q(db, plan[name], plan["currency"]))
        return {"settlement": shown, "message": "Dry run: nothing was saved"}
    settlement = svc.run_for_tenant(db, payload.tenant_id, now, actor=actor, force=payload.force)
    return {"settlement": payment_view.settlement_response(settlement, actor, db) if settlement else None,
            "message": "Settlement created" if settlement else "Nothing was settled: no items, a hold, or below the minimum payout"}


@router.get("", response_model=SettlementListResponse, summary="Settlement history",
            description="FR-SET-02. A business sees its own payouts, the platform all.")
def settlement_history(
    status_filter: str | None = Query(None, alias="status"), tenant_id: UUID | None = Query(None, description="Super Admin only"), date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200), db: Session = Depends(get_db),
    actor: PayActor = Depends(get_pay_actor),
):
    rows, total = svc.list_settlements(db, actor, status=status_filter, tenant_id=tenant_id, date_from=date_from, date_to=date_to, page=page, page_size=page_size)
    return SettlementListResponse(items=[payment_view.settlement_response(r, actor, db) for r in rows], total=total, page=page, page_size=page_size)


@router.get("/{settlement_id}", response_model=SettlementDetailResponse, summary="One settlement with every payment and refund in it",
            description="FR-SET-07. The itemised breakdown: for each payment the gross, the commission, the gateway fee and the net.")
def get_settlement(settlement_id: UUID, db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    row = svc.get_settlement(db, actor, settlement_id)
    base = payment_view.settlement_response(row, actor, db)
    return SettlementDetailResponse(**base.model_dump(), items=[payment_view.settlement_item_response(db, i, row) for i in svc.items_of(db, row)])


@router.get("/{settlement_id}/verify", summary="Check the settlement against its payments and refunds",
            description="FR-SET-06. Recomputes the totals from the items and compares every item with the payment or refund behind it. `reconciled: true` means a 1:1 match.")
def verify_settlement(settlement_id: UUID, db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    return svc.verify(db, svc.get_settlement(db, actor, settlement_id))


@router.get("/{settlement_id}/export", summary="Download the settlement as CSV",
            description="FR-SET-03. One line per payment or refund, for the accounts.", responses={200: {"content": {"text/csv": {}}}})
def export_settlement(settlement_id: UUID, db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    row = svc.get_settlement(db, actor, settlement_id)
    items = [{"reference": row.reference, "currency": row.currency, "kind": i.kind, "payment_id": i.payment_id, "refund_id": i.refund_id, "charge_currency": i.charge_currency,
              "charge_amount": i.charge_amount, "gross": i.gross, "commission": i.commission, "gateway_fee": i.fee, "fixed_fee": i.fixed_fee, "net": i.net} for i in svc.items_of(db, row)]
    return payment_export.csv_response(items, ["reference", "currency", "kind", "payment_id", "refund_id", "charge_currency", "charge_amount", "gross", "commission",
                                              "gateway_fee", "fixed_fee", "net"], f"{row.reference}.csv")


@router.post("/{settlement_id}/pay", response_model=SettlementResponse, summary="Record that the payout was made (Super Admin)",
             description="FR-SET-01. Finance sends the money through the bank or payout service and records the reference here. The business is notified.")
def pay_settlement(settlement_id: UUID, payload: PayoutBody = Body(...), db: Session = Depends(get_db), actor: PayActor = Depends(require_super_admin)):
    return payment_view.settlement_response(svc.mark_paid(db, actor, settlement_id, payload.payout_reference, access_token=actor.token), actor, db)


@router.post("/{settlement_id}/fail", response_model=SettlementResponse, summary="Record that the payout failed (Super Admin)")
def fail_settlement(settlement_id: UUID, payload: ReasonBody = Body(...), db: Session = Depends(get_db), actor: PayActor = Depends(require_super_admin)):
    return payment_view.settlement_response(svc.mark_failed(db, actor, settlement_id, payload.reason), actor, db)


@router.post("/{settlement_id}/cancel", response_model=SettlementResponse, summary="Cancel an unpaid settlement (Super Admin)",
             description="Its payments and refunds go back to being unsettled and join the next settlement.")
def cancel_settlement(settlement_id: UUID, payload: ReasonBody = Body(...), db: Session = Depends(get_db), actor: PayActor = Depends(require_super_admin)):
    return payment_view.settlement_response(svc.cancel(db, actor, settlement_id, payload.reason), actor, db)
