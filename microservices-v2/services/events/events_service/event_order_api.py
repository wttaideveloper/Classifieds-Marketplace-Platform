"""Event orders as seen by commerce (design D2).

GET  /event-orders/{id}                      what commerce needs to build the payable (monolith: payment_payables._event_order
                                              read the row directly)
POST /event-orders/{id}/payment-confirmed    from commerce's outbox once the payment succeeded (monolith: on_paid,
                                              same transaction as the payment)
POST /event-orders/{id}/refunded             from commerce's outbox after a refund (monolith: on_refunded)
Both POSTs carry an Idempotency-Key and are applied once.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from events_service.db.database import Base, get_db
from events_service.models.event_aux_models import EventOrder
from mskit.app import require_internal_key
from mskit.outbox import first_time, idempotency_key, models_for

models_for(Base)
router = APIRouter(dependencies=[Depends(require_internal_key)], tags=["internal"])


def _s(value):
    return None if value is None else str(value)


@router.get("/event-orders/{order_id}")
def payable_view(order_id: UUID, db: Session = Depends(get_db)):
    order = db.get(EventOrder, order_id)
    if order is None:
        raise HTTPException(404, "Order not found")
    event = order.event
    return {
        "id": str(order.id), "participant_email": order.participant_email, "status": order.status,
        "payment_status": order.payment_status, "currency": order.currency, "amount": _s(order.amount),
        "ticket_subtotal": _s(order.ticket_subtotal), "meal_subtotal": _s(order.meal_subtotal),
        "accommodation_subtotal": _s(order.accommodation_subtotal),
        "event": {"title": event.title, "tenant_id": _s(event.tenant_id)} if event is not None else None,
    }


class Confirmed(BaseModel):
    payment_id: str | None = None


class Refunded(BaseModel):
    payment_id: str | None = None
    fully: bool = True


@router.post("/event-orders/{order_id}/payment-confirmed")
def payment_confirmed(order_id: UUID, body: Confirmed, request: Request, db: Session = Depends(get_db)):
    if first_time(db, idempotency_key(request), Base):
        order = db.get(EventOrder, order_id)
        if order is not None:
            order.payment_status = "confirmed"
            order.payment_provider = "marketplace"
    db.commit()
    return {"applied": True}


@router.post("/event-orders/{order_id}/refunded")
def refunded(order_id: UUID, body: Refunded, request: Request, db: Session = Depends(get_db)):
    if first_time(db, idempotency_key(request), Base) and body.fully:
        order = db.get(EventOrder, order_id)
        if order is not None:
            order.payment_status = "refunded"
            order.status = "refunded"
    db.commit()
    return {"applied": True}
