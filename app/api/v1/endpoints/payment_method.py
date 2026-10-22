from datetime import date
from uuid import UUID

from fastapi import APIRouter, Body, Depends, status
from sqlalchemy.orm import Session

from app.core.payment_access import PayActor, get_pay_actor
from app.db.database import get_db
from app.schemas.payment_schema import SavedMethodCreate, SavedMethodListResponse, SavedMethodResponse
from app.services import payment_method_service as svc

router = APIRouter(tags=["Payments"])


def _out(row) -> SavedMethodResponse:
    out = SavedMethodResponse.model_validate(row)
    out.is_expired = svc.is_expired(row, date.today())
    return out


@router.get("", response_model=SavedMethodListResponse, summary="My saved payment methods", description="FR-PAY-04. Brand, last four digits and expiry only.")
def list_saved_methods(db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    rows = svc.list_methods(db, actor.user_uuid)
    return SavedMethodListResponse(items=[_out(r) for r in rows], total=len(rows))


@router.post("", response_model=SavedMethodResponse, status_code=status.HTTP_201_CREATED, summary="Save a payment method",
             description="Send the token the gateway's SDK returned (Stripe pm_..., Razorpay token id). A card number is never accepted: a request that carries card fields is refused. "
                         "Test gateway: tok_saved_visa_4242.")
def save_payment_method(payload: SavedMethodCreate = Body(...), db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    return _out(svc.save_method(db, actor, payload))


@router.put("/{method_id}/default", response_model=SavedMethodResponse, summary="Make this my default payment method")
def make_default(method_id: UUID, db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    return _out(svc.set_default(db, actor, method_id))


@router.delete("/{method_id}", summary="Remove a saved payment method")
def remove_payment_method(method_id: UUID, db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    svc.delete_method(db, actor, method_id)
    return {"message": "Payment method removed"}
