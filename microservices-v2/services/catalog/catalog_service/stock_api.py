"""Stock reservation API (design D1). The checks, their order and their messages are the monolith's
(app/services/cart_service.py: checkout_service + _check_stock + _effective_price)."""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from catalog_service.db.database import Base, SessionLocal, get_db
from catalog_service.models.product_model import Product
from catalog_service.models.stock_reservation_model import StockReservation
from mskit.app import require_internal_key
from mskit.outbox import first_time, idempotency_key, models_for

logger = logging.getLogger(__name__)
models_for(Base)  # processed_messages: a duplicate delivery is applied once
router = APIRouter(dependencies=[Depends(require_internal_key)], tags=["internal"])
DEFAULT_TTL_SECONDS = 900


class Line(BaseModel):
    product_id: UUID
    quantity: int


class ReserveRequest(BaseModel):
    key: str
    items: list[Line]
    ttl_seconds: int = DEFAULT_TTL_SECONDS


class ConfirmRequest(BaseModel):
    order_ref: str | None = None


class ReleaseRequest(BaseModel):
    items: list[dict]   # [{"product_id", "stock_debited"}] - what an order had taken


def _effective_price(product: Product) -> tuple[float, str]:
    price = product.sale_price if product.sale_price is not None else product.product_price
    currency = product.currency or "USD"
    if price is None:
        price = product.product_price or 0
    return float(price), currency


def _check_stock(product: Product, desired_qty: int):
    if product.is_deleted or product.status == "inactive":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Product is not available")
    if product.stock_quantity is not None and desired_qty > product.stock_quantity:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Insufficient stock. Available: {product.stock_quantity}")


def _answer(res: StockReservation) -> dict:
    return {"reservation_id": res.id, "status": res.status, "items": res.items}


@router.post("/stock/reservations")
def reserve(body: ReserveRequest, db: Session = Depends(get_db)):
    existing = db.query(StockReservation).filter(StockReservation.idempotency_key == body.key[:255]).first()
    if existing is not None:   # a retried request: the same answer, nothing taken twice
        return _answer(existing)
    ids = [line.product_id for line in body.items]
    products = {p.id: p for p in db.query(Product).filter(Product.id.in_(ids)).order_by(Product.id).populate_existing().with_for_update().all()}
    priced = []
    for line in body.items:  # cart order, exactly like the monolith's loop
        product = products.get(line.product_id)
        if not product or product.is_deleted:
            db.rollback()
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Product {line.product_id} is not available")
        try:
            _check_stock(product, line.quantity)
        except HTTPException:
            db.rollback()
            raise
        unit_price, cur = _effective_price(product)
        priced.append({"product_id": str(product.id), "quantity": line.quantity, "unit_price": unit_price, "currency": cur,
                       "product_name": product.product_name, "sku": product.sku, "stock_debited": None})
    for row in priced:
        product = products[UUID(row["product_id"])]
        if product.stock_quantity is not None:
            product.stock_quantity -= row["quantity"]
            row["stock_debited"] = row["quantity"]
    res = StockReservation(idempotency_key=body.key[:255], items=priced, status="held",
                           expires_at=datetime.utcnow() + timedelta(seconds=max(60, body.ttl_seconds)))
    db.add(res)
    db.commit()
    return _answer(res)


def _restore(db: Session, items: list[dict], sign: int = 1) -> None:
    """Give back (sign=1) or take again (sign=-1) what a reservation/order debited. ORM updates under row locks, so
    the change is pushed to the product replicas (commerce) like any other product change."""
    for row in sorted(items, key=lambda r: str(r["product_id"])):
        debited = row.get("stock_debited")
        if debited:
            product = db.query(Product).filter(Product.id == UUID(str(row["product_id"]))).with_for_update().first()
            if product is not None and product.stock_quantity is not None:
                product.stock_quantity = product.stock_quantity + sign * int(debited)


@router.post("/stock/reservations/{reservation_id}/confirm")
def confirm(reservation_id: str, body: ConfirmRequest, db: Session = Depends(get_db)):
    res = db.get(StockReservation, reservation_id, with_for_update=True)
    if res is None:
        raise HTTPException(404, "Reservation not found")
    if res.status == "released":
        # it expired before commerce confirmed it, but the order exists: take the stock again
        logger.warning("reservation %s confirmed after release; debiting again for order %s", res.id, body.order_ref)
        _restore(db, res.items, sign=-1)
    if res.status in ("held", "released"):
        res.status, res.order_ref = "confirmed", body.order_ref
        db.commit()
    return {"reservation_id": res.id, "status": res.status}


@router.delete("/stock/reservations/{reservation_id}")
def release(reservation_id: str, db: Session = Depends(get_db)):
    """Commerce could not create the order: give the stock back (no effect once confirmed or released)."""
    res = db.get(StockReservation, reservation_id, with_for_update=True)
    if res is None:
        raise HTTPException(404, "Reservation not found")
    if res.status == "held":
        _restore(db, res.items)
        res.status = "released"
        db.commit()
    return {"reservation_id": res.id, "status": res.status}


@router.post("/stock/releases")
def restock(body: ReleaseRequest, request: Request, db: Session = Depends(get_db)):
    """Order cancelled / refunded: put back what it had taken (monolith: order_service restock). Idempotent."""
    if first_time(db, idempotency_key(request), Base):
        _restore(db, body.items)
    db.commit()
    return {"restocked": True}


def expire_once() -> int:
    """Release held reservations past their expiry (commerce never confirmed them)."""
    n = 0
    with SessionLocal() as db:
        for res in db.query(StockReservation).filter(StockReservation.status == "held", StockReservation.expires_at < datetime.utcnow()).with_for_update().all():
            _restore(db, res.items)
            res.status = "released"
            n += 1
        db.commit()
    if n:
        logger.warning("released %d expired stock reservation(s)", n)
    return n


def start_sweeper() -> None:
    if os.environ.get("DISABLE_BACKGROUND_JOBS", "").lower() in ("1", "true", "yes"):
        return

    def loop():
        while True:
            try:
                expire_once()
            except Exception:  # noqa: BLE001
                logger.exception("stock reservation sweep failed")
            time.sleep(60)

    threading.Thread(target=loop, name="stock-reservation-sweeper", daemon=True).start()
