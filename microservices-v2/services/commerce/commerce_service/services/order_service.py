"""Shop orders after checkout: cancelling, refund eligibility, fulfilment (the Enterprise Admin's side), stock and the order's answers
(docs/order-cancellation-and-refunds.md).

Statuses:  pending (not paid)  ->  confirmed (paid)  ->  processing  ->  shipped (carrier + tracking number)  ->  delivered  ->  completed
           cancelled, cancellation_requested, refunded

Customer
  * cancels an unpaid (pending) order at once;
  * asks to cancel a paid order that has not shipped (cancellation_requested: the Enterprise Admin approves or rejects);
  * cannot cancel once it has shipped; within ORDER_REFUND_WINDOW_DAYS (7) of delivery asks for a refund through POST /api/v1/refunds.
Enterprise Admin (of the order's tenant) and Super Admin: see the orders, move them along, decide cancellation requests. A Provider and a customer cannot.

Money: approving a cancellation only changes statuses (and gives the stock back). Refunding a paid order is the business's refund through /api/v1/refunds
(`refund_due` on the order says one is owed); a fully completed refund sets the order to `refunded`.
Stock: checkout takes the items out of stock; cancelling before shipment gives them back, once (`stock_reduced` / `stock_restocked`).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session, joinedload

from mskit.settings import settings
from commerce_service.models.cart_model import Order, OrderItem, OrderStatusEvent
from commerce_service.models.product_model import Product

logger = logging.getLogger(__name__)

TERMINAL = ("cancelled", "refunded", "completed")
ADMIN_TRANSITIONS = {
    "pending": ("confirmed", "cancelled"),
    "confirmed": ("processing", "shipped", "cancelled"),
    "processing": ("shipped", "cancelled"),
    "shipped": ("delivered",),
    "delivered": ("completed",),
}
CANCELLABLE_BY_STAFF = ("pending", "confirmed", "processing")
PAID_PAYMENT = ("succeeded", "partially_refunded", "refunded")
OPEN_PAYMENT = ("created", "requires_action", "processing", "authorized", "review")
OPEN_REFUND = ("requested", "approved", "processing")
REFUND_STATUS_FOR_APP = {"requested": "requested", "approved": "approved", "processing": "approved", "completed": "completed", "rejected": "rejected", "failed": "failed"}


def _err(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"message": message, "code": code, **extra})


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def money(value) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('0.01')):f}"


# ---------------------------------------------------------------- who is calling

def staff_actor(db: Session, user: dict, access_token: str | None = None):
    """The caller as an Enterprise Admin or Super Admin (403 for anyone else, a Provider included)."""
    from commerce_service.services.actor import actor_from

    actor = actor_from(db, user, access_token)
    if actor.role not in ("admin", "super_admin"):
        raise _err(403, "ADMIN_ONLY", "Enterprise Admin access required")
    return actor


def _may_manage(actor, order: Order) -> bool:
    return actor.role == "super_admin" or (actor.role == "admin" and order.tenant_id is not None and order.tenant_id == actor.tenant_id)


def staff_order(db: Session, actor, order_id: UUID) -> Order:
    order = db.query(Order).options(joinedload(Order.items)).filter(Order.id == order_id, Order.is_deleted.is_(False)).first()
    if order is None or not _may_manage(actor, order):
        raise _err(404, "ORDER_NOT_FOUND", "Order not found")
    return order


def own_order(db: Session, user_id: UUID, order_id: UUID) -> Order:
    order = db.query(Order).options(joinedload(Order.items)).filter(Order.id == order_id, Order.user_id == user_id, Order.is_deleted.is_(False)).first()
    if order is None:
        raise _err(404, "ORDER_NOT_FOUND", "Order not found")
    return order


# ---------------------------------------------------------------- history, stock

def log(db: Session, order: Order, action: str, actor_id, actor_role: str | None, *, from_status=None, to_status=None, reason=None, detail=None) -> None:
    db.add(OrderStatusEvent(order_id=order.id, action=action, from_status=from_status, to_status=to_status, actor_id=actor_id, actor_role=actor_role or "system",
                            reason=reason, detail=detail))


def _move(db: Session, order: Order, new_status: str, action: str, actor_id, actor_role, reason=None, detail=None) -> None:
    previous = order.status
    order.status = new_status
    order.updated_at = now_utc()
    log(db, order, action, actor_id, actor_role, from_status=previous, to_status=new_status, reason=reason, detail=detail)


def restock(db: Session, order: Order) -> bool:
    """Give the order's items back to stock, once, if checkout took them. True when stock changed."""
    if not order.stock_reduced or order.stock_restocked:
        return False
    # Stock belongs to catalog (D1): the release is written to the outbox in this transaction and delivered
    # right after the commit (retried until catalog applied it once).
    from commerce_service.outbox_setup import OUTBOX

    released = []
    for item in sorted(order.items, key=lambda item: str(item.product_id)):
        if item.stock_debited:
            released.append({"product_id": str(item.product_id), "stock_debited": int(item.stock_debited)})
            item.stock_debited = 0
    if released:
        OUTBOX.enqueue(db, "catalog", "POST", "/stock/releases", {"items": released}, f"order:{order.id}:restock")
    order.stock_restocked = True
    return True


# ---------------------------------------------------------------- payment and refund of an order

def payment_of(db: Session, order: Order):
    """The order's payment (the one that was paid if any, else the latest) and its latest refund."""
    from commerce_service.models.payment_model import Payment, Refund

    payments = db.query(Payment).filter(Payment.purchase_type == "cart_order", Payment.purchase_id == str(order.id)).order_by(Payment.created_at.desc()).all()
    payment = next((p for p in payments if p.status in PAID_PAYMENT), payments[0] if payments else None)
    refund = db.query(Refund).filter(Refund.payment_id == payment.id).order_by(Refund.created_at.desc()).first() if payment is not None else None
    return payment, refund


def payment_status_of(payment) -> str:
    if payment is None:
        return "unpaid"
    if payment.status == "succeeded":
        return "paid"
    if payment.status in ("partially_refunded", "refunded"):
        return payment.status
    return "pending" if payment.status in OPEN_PAYMENT else "unpaid"


def cancel_decision(order: Order, payment) -> dict:
    """What cancelling does now: {"mode": instant | request | blocked, "reason"}."""
    if order.status in ("cancelled", "refunded"):
        return {"mode": "blocked", "reason": f"This order is already {order.status}"}
    if order.status == "cancellation_requested":
        return {"mode": "blocked", "reason": "A cancellation request is already waiting for the store"}
    if order.status in ("shipped",):
        return {"mode": "blocked", "reason": "This order has already been shipped, so it can no longer be cancelled"}
    if order.status in ("delivered", "completed"):
        return {"mode": "blocked", "reason": f"This order was delivered. You can ask for a refund within {settings.ORDER_REFUND_WINDOW_DAYS} days of delivery"}
    if order.status == "pending":
        if payment is not None and payment.status in PAID_PAYMENT:
            return {"mode": "request", "reason": None}
        if payment is not None and payment.status in OPEN_PAYMENT:
            return {"mode": "blocked", "reason": "A payment for this order is in progress. Try again in a few minutes"}
        return {"mode": "instant", "reason": None}
    return {"mode": "request", "reason": None}        # confirmed / processing: paid, not shipped


def refund_decision(db: Session, order: Order, payment, refund, now: datetime | None = None) -> dict:
    """Whether the customer can ask for a refund now: {"allowed", "reason"}."""
    now = now or now_utc()
    if order.status in ("cancelled", "refunded"):
        return {"allowed": False, "reason": f"This order is {order.status}"}
    if order.status == "cancellation_requested":
        return {"allowed": False, "reason": "A cancellation request is already waiting for the store"}
    if order.status == "pending":
        return {"allowed": False, "reason": "Nothing has been paid for this order"}
    if order.status in ("confirmed", "processing"):
        return {"allowed": False, "reason": "This order has not shipped yet: cancel it instead"}
    if order.status == "shipped":
        return {"allowed": False, "reason": f"Refunds open after delivery (within {settings.ORDER_REFUND_WINDOW_DAYS} days)"}
    if payment is None or payment.status not in ("succeeded", "partially_refunded"):
        return {"allowed": False, "reason": "There is no paid payment to refund for this order"}
    delivered = order.delivered_at
    if delivered is None:
        return {"allowed": False, "reason": "Delivery date is unknown; contact support"}
    if now > delivered + timedelta(days=settings.ORDER_REFUND_WINDOW_DAYS):
        return {"allowed": False, "reason": f"The {settings.ORDER_REFUND_WINDOW_DAYS}-day refund period after delivery has ended"}
    from commerce_service.services import refund_service

    if refund_service.refundable(db, payment) <= 0:
        return {"allowed": False, "reason": "There is nothing left to refund on this order"}
    return {"allowed": True, "reason": None}


# ---------------------------------------------------------------- the answers

def _items(order: Order) -> list[dict]:
    return [{"id": i.id, "product_id": i.product_id, "product_name": i.product_name, "sku": i.sku, "quantity": i.quantity, "unit_price": i.unit_price,
             "line_total": i.line_total, "currency": i.currency} for i in order.items]


def order_view(db: Session, order: Order, *, staff: bool = False, detail: bool = True) -> dict:
    """The order as the customer sees it (staff=False) or as staff do."""
    payment, refund = payment_of(db, order)
    cancel = cancel_decision(order, payment)
    refund_rule = refund_decision(db, order, payment, refund)
    paid = payment is not None and payment.status in PAID_PAYMENT
    refunded_in_full = payment is not None and payment.status == "refunded"
    view = {
        "id": order.id, "user_id": order.user_id, "tenant_id": order.tenant_id, "status": order.status, "total": order.total, "currency": order.currency,
        "shipping_address": order.shipping_address, "items": _items(order), "created_at": order.created_at, "updated_at": order.updated_at,
        "payment_id": payment.id if payment is not None else None, "payment_status": payment_status_of(payment),
        "can_cancel": cancel["mode"] != "blocked", "cancel_mode": cancel["mode"] if cancel["mode"] != "blocked" else None, "cancel_blocked_reason": cancel["reason"],
        "can_refund": refund_rule["allowed"], "refund_blocked_reason": refund_rule["reason"], "refund_window_days": settings.ORDER_REFUND_WINDOW_DAYS,
        "refund_id": refund.id if refund is not None else None,
        "refund_status": REFUND_STATUS_FOR_APP.get(refund.status, refund.status) if refund is not None else None,
        "refund_amount": money(refund.amount) if refund is not None else None, "refund_currency": refund.currency if refund is not None else None,
        # a paid order that was cancelled and has no completed refund: the store still owes the customer money
        "refund_due": bool(order.status == "cancelled" and paid and not refunded_in_full and not (refund is not None and refund.status in OPEN_REFUND + ("completed",))),
        "carrier": order.carrier, "tracking_number": order.tracking_number, "tracking_url": order.tracking_url, "shipped_at": order.shipped_at, "delivered_at": order.delivered_at,
        "cancellation": {"requested_at": order.cancellation_requested_at, "reason": order.cancel_reason, "decision_reason": order.decision_reason,
                         "cancelled_at": order.cancelled_at, "cancelled_by_role": order.cancelled_by_role},
    }
    if not detail:
        return view
    view["history"] = [{"action": e.action, "from_status": e.from_status, "to_status": e.to_status, "actor_role": e.actor_role, "reason": e.reason, "at": e.created_at}
                       for e in order.history]
    if staff:
        view["allowed_statuses"] = [] if order.status == "cancellation_requested" else list(ADMIN_TRANSITIONS.get(order.status, ()))
        view["customer"] = {"user_id": order.user_id, "name": order.shipping_full_name, "phone": order.shipping_phone}
    return view


# ---------------------------------------------------------------- customer cancels

def cancel_order(db: Session, user_id: UUID, order_id: UUID, reason: str | None, access_token: str | None = None) -> Order:
    order = own_order(db, user_id, order_id)
    payment, _refund = payment_of(db, order)
    decision = cancel_decision(order, payment)
    if decision["mode"] == "blocked":
        raise _err(409, "CANCEL_NOT_ALLOWED", decision["reason"])
    reason = " ".join((reason or "").split()) or None
    if decision["mode"] == "request":
        if not reason or len(reason) < 3:
            raise _err(422, "REASON_REQUIRED", "Tell the store why you want to cancel (at least 3 characters)")
        order.status_before_request = order.status
        order.cancel_reason, order.cancellation_requested_at = reason, now_utc()
        _move(db, order, "cancellation_requested", "cancellation_requested", user_id, "customer", reason)
        db.commit()
        db.refresh(order)
        _notify("cancellation_requested", db, order, access_token=access_token)
        return order
    order.cancel_reason, order.cancelled_at, order.cancelled_by, order.cancelled_by_role = reason, now_utc(), user_id, "customer"
    restock(db, order)
    _move(db, order, "cancelled", "cancelled", user_id, "customer", reason)
    db.commit()
    db.refresh(order)
    _notify("cancelled", db, order, by_customer=True, access_token=access_token)
    return order


# ---------------------------------------------------------------- staff: status, shipping, decisions

def change_status(db: Session, actor, order_id: UUID, new_status: str, reason: str | None = None, carrier: str | None = None,
                  tracking_number: str | None = None, tracking_url: str | None = None, access_token: str | None = None) -> Order:
    order = staff_order(db, actor, order_id)
    if order.status == "cancellation_requested":
        raise _err(409, "DECIDE_THE_REQUEST", "This order has a cancellation request: approve or reject it")
    allowed = list(ADMIN_TRANSITIONS.get(order.status, ()))
    if new_status not in allowed:
        raise _err(409, "INVALID_TRANSITION", f"A {order.status} order cannot become {new_status}", allowed=allowed)
    reason = " ".join((reason or "").split()) or None
    now = now_utc()
    if new_status == "shipped":
        carrier, tracking_number = (carrier or "").strip() or None, (tracking_number or "").strip() or None
        if not carrier or not tracking_number:
            raise _err(422, "TRACKING_REQUIRED", "A carrier and a tracking number are required to ship an order")
        order.carrier, order.tracking_number, order.tracking_url, order.shipped_at = carrier, tracking_number, (tracking_url or "").strip() or None, now
    if new_status == "delivered":
        order.delivered_at = now
    if new_status == "cancelled":
        if not reason or len(reason) < 3:
            raise _err(422, "REASON_REQUIRED", "A reason is required to cancel an order (at least 3 characters)")
        order.cancel_reason, order.cancelled_at, order.cancelled_by, order.cancelled_by_role = reason, now, actor.id, actor.role
        restock(db, order)
    _move(db, order, new_status, new_status, actor.id, actor.role, reason, {"carrier": carrier, "tracking_number": tracking_number} if new_status == "shipped" else None)
    db.commit()
    db.refresh(order)
    _notify({"processing": "processing", "shipped": "shipped", "delivered": "delivered", "cancelled": "cancelled"}.get(new_status, ""), db, order, access_token=access_token)
    return order


def update_shipping(db: Session, actor, order_id: UUID, carrier: str | None, tracking_number: str | None, tracking_url: str | None, access_token: str | None = None) -> Order:
    order = staff_order(db, actor, order_id)
    if order.status != "shipped":
        raise _err(409, "NOT_SHIPPED", "Tracking can be changed while the order is shipped")
    carrier, tracking_number = (carrier or order.carrier or "").strip(), (tracking_number or order.tracking_number or "").strip()
    if not carrier or not tracking_number:
        raise _err(422, "TRACKING_REQUIRED", "A carrier and a tracking number are required")
    order.carrier, order.tracking_number = carrier, tracking_number
    order.tracking_url = (tracking_url if tracking_url is not None else order.tracking_url) or None
    order.updated_at = now_utc()
    log(db, order, "tracking_updated", actor.id, actor.role, detail={"carrier": carrier, "tracking_number": tracking_number})
    db.commit()
    db.refresh(order)
    _notify("shipped", db, order, access_token=access_token)
    return order


def decide_cancellation(db: Session, actor, order_id: UUID, approve: bool, reason: str | None, access_token: str | None = None) -> Order:
    order = staff_order(db, actor, order_id)
    if order.status != "cancellation_requested":
        raise _err(409, "NO_REQUEST", "There is no cancellation request waiting on this order")
    reason = " ".join((reason or "").split()) or None
    if approve:
        order.cancelled_at, order.cancelled_by, order.cancelled_by_role, order.decision_reason = now_utc(), actor.id, actor.role, reason
        restock(db, order)
        _move(db, order, "cancelled", "cancellation_approved", actor.id, actor.role, reason)
        db.commit()
        db.refresh(order)
        _notify("cancelled", db, order, access_token=access_token)
        return order
    if not reason or len(reason) < 3:
        raise _err(422, "REASON_REQUIRED", "A reason is required to reject a cancellation request (at least 3 characters)")
    order.decision_reason = reason
    _move(db, order, order.status_before_request or "confirmed", "cancellation_rejected", actor.id, actor.role, reason)
    order.status_before_request = None
    db.commit()
    db.refresh(order)
    _notify("cancellation_rejected", db, order, access_token=access_token)
    return order


def on_refunded(db: Session, order: Order, *, was_status: str) -> None:
    """A full refund completed (payments module): the order is `refunded`. Items that never shipped go back to stock; a delivered order's items are not assumed returned."""
    previous = order.status
    if previous == "refunded":
        return
    order.status = "refunded"
    order.updated_at = now_utc()
    if was_status in ("pending", "confirmed", "processing", "cancellation_requested", "cancelled"):
        restock(db, order)
    log(db, order, "refunded", None, "system", from_status=previous, to_status="refunded")


def on_paid(db: Session, order: Order) -> None:
    log(db, order, "paid", None, "system", from_status="pending", to_status="confirmed")


# ---------------------------------------------------------------- staff lists

def list_for_staff(db: Session, actor, *, status: str | None = None, q: str | None = None, enterprise_id: UUID | None = None, tenant_id: UUID | None = None,
                   date_from=None, date_to=None, page: int = 1, page_size: int = 20, only_requests: bool = False) -> dict:
    query = db.query(Order).options(joinedload(Order.items)).filter(Order.is_deleted.is_(False))
    if actor.role == "admin":
        query = query.filter(Order.tenant_id == actor.tenant_id)
    elif tenant_id:
        query = query.filter(Order.tenant_id == tenant_id)
    if only_requests:
        query = query.filter(Order.status == "cancellation_requested").order_by(Order.cancellation_requested_at.asc())
    else:
        if status:
            query = query.filter(Order.status.in_([s.strip() for s in status.split(",") if s.strip()]))
        query = query.order_by(Order.created_at.desc())
    if enterprise_id:
        query = query.filter(Order.id.in_(db.query(OrderItem.order_id).join(Product, Product.id == OrderItem.product_id).filter(Product.enterprise_id == enterprise_id)))
    if q and q.strip():
        term = q.strip().lower()
        like = f"%{term}%"
        query = query.filter(or_(func.lower(Order.shipping_full_name).like(like), func.lower(Order.shipping_phone).like(like), func.lower(Order.tracking_number).like(like),
                                 func.lower(cast(Order.id, String)).like(f"{term}%"),
                                 Order.id.in_(db.query(OrderItem.order_id).filter(func.lower(OrderItem.product_name).like(like)))))
    if date_from:
        query = query.filter(Order.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        query = query.filter(Order.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
    total = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    items = []
    for o in rows:
        payment, _refund = payment_of(db, o)
        items.append({
            "id": o.id, "status": o.status, "payment_status": payment_status_of(payment), "total": o.total, "currency": o.currency, "created_at": o.created_at,
            "customer": {"user_id": o.user_id, "name": o.shipping_full_name, "phone": o.shipping_phone}, "item_count": len(o.items),
            "items_preview": [i.product_name for i in o.items[:3]], "carrier": o.carrier, "tracking_number": o.tracking_number,
            "cancellation_requested_at": o.cancellation_requested_at, "city": o.shipping_city,
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ---------------------------------------------------------------- notifications (best effort, after the change is saved)

def _notify(kind: str, db: Session, order: Order, by_customer: bool = False, access_token: str | None = None) -> None:
    if not kind:
        return
    try:
        from commerce_service.services import order_notifications

        order_notifications.send(db, order, kind, by_customer=by_customer, access_token=access_token)
    except Exception:
        logger.exception("order notification %s failed", kind)
