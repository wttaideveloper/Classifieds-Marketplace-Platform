from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.exceptions.custom_exception import CustomException
from app.schemas.order_schema import CreateOrderSchema, UpdateOrderStatusSchema
from app.services.order_service import (
    create_order_service,
    get_customer_orders_service,
    get_merchant_orders_service,
    get_order_details_service,
    update_order_status_service,
)

router = APIRouter(tags=["Orders"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("")
def create_order(
    payload: CreateOrderSchema,
    customer_id: str = Query(..., description="Customer UUID"),
    db: Session = Depends(get_db),
):
    return create_order_service(db=db, customer_id=customer_id, payload=payload)


@router.get("/{order_id}")
def get_order(order_id: str, db: Session = Depends(get_db)):
    return get_order_details_service(db=db, order_id=order_id)


@router.get("")
def list_orders(
    customer_id: str = Query(None, description="Customer UUID"),
    merchant_id: str = Query(None, description="Merchant UUID"),
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    if customer_id:
        return get_customer_orders_service(db=db, customer_id=customer_id, page=page, size=size)
    if merchant_id:
        return get_merchant_orders_service(db=db, merchant_id=merchant_id, page=page, size=size)
    raise CustomException(400, "Pass either customer_id or merchant_id")


@router.put("/{order_id}/status")
def update_order_status(
    order_id: str,
    payload: UpdateOrderStatusSchema,
    merchant_id: str = Query(..., description="Merchant UUID"),
    db: Session = Depends(get_db),
):
    return update_order_status_service(
        db=db,
        order_id=order_id,
        merchant_id=merchant_id,
        payload=payload,
    )

