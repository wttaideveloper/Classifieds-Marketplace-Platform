from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.payment_access import PayActor, get_pay_actor
from app.db.database import get_db
from app.schemas.refund_schema import ManualCompletion, RefundCreate, RefundDecision, RefundListResponse, RefundResponse
from app.services import payment_view, refund_service as svc

router = APIRouter(tags=["Refunds"])


@router.post("", response_model=RefundResponse, status_code=status.HTTP_201_CREATED, summary="Ask for a refund, or refund a payment",
             description="FR-REF-01, FR-REF-02. A customer's request waits for the business (or platform) to approve it. When the business or platform refunds, it is approved and sent to the "
                         "gateway at once. Leave `amount` out for a full refund of what is left. A credit note, the tax reversal and the order update follow when it completes.")
def create_refund(payload: RefundCreate = Body(...), db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    refund = svc.request_refund(db, actor, payload, access_token=actor.token)
    return payment_view.refund_response(db, refund, actor)


@router.get("", response_model=RefundListResponse, summary="Refund history",
            description="FR-REF-04. A customer sees their own refunds, a business those of its payments, the platform all. `overdue=true` lists the ones past their target time.")
def refund_history(
    status_filter: str | None = Query(None, alias="status"), payment_id: UUID | None = Query(None), tenant_id: UUID | None = Query(None, description="Super Admin only"),
    overdue: bool | None = Query(None), date_from: datetime | None = Query(None), date_to: datetime | None = Query(None), page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200), db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor),
):
    rows, total = svc.list_refunds(db, actor, status=status_filter, payment_id=payment_id, tenant_id=tenant_id, overdue=overdue, date_from=date_from, date_to=date_to,
                                   page=page, page_size=page_size)
    return RefundListResponse(items=[payment_view.refund_response(db, r, actor) for r in rows], total=total, page=page, page_size=page_size)


@router.get("/{refund_id}", response_model=RefundResponse, summary="Refund status", description="FR-REF-03. Status, where the money goes, the target time and the credit note.")
def get_refund(refund_id: UUID, db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    return payment_view.refund_response(db, svc.get_refund(db, actor, refund_id), actor)


@router.post("/{refund_id}/decision", response_model=RefundResponse, summary="Approve or reject a refund request (business / platform)",
             description="Approving sends it to the gateway. Rejecting needs a reason, which the customer reads.")
def decide_refund(refund_id: UUID, payload: RefundDecision = Body(...), db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    return payment_view.refund_response(db, svc.decide_refund(db, actor, refund_id, payload.action, payload.reason, access_token=actor.token), actor)


@router.post("/{refund_id}/retry", response_model=RefundResponse, summary="Send a failed or stuck refund to the gateway again (business / platform)",
             description="Safe to repeat: the gateway gets the same idempotency key, so the customer is never refunded twice.")
def retry_refund(refund_id: UUID, db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    return payment_view.refund_response(db, svc.retry_refund(db, actor, refund_id, access_token=actor.token), actor)


@router.post("/{refund_id}/complete-manual", response_model=RefundResponse, summary="Record a bank-transfer refund as paid (Super Admin)",
             description="FR-REF-06. For a refund to a bank account, which finance pays outside the gateway.")
def complete_manual(refund_id: UUID, payload: ManualCompletion = Body(...), db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    return payment_view.refund_response(db, svc.complete_manual(db, actor, refund_id, payload.bank_reference, access_token=actor.token), actor)
