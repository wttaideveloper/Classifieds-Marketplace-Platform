"""What can be paid for, and what happens to it when it is paid or refunded.

A payment never trusts an amount from the client. It names *what* is being bought and this module reads the price from the
order itself:

    cart_order   a shop order (orders / order_items), paid by the customer who placed it; the order is confirmed when paid
    event_order  an event registration order, paid by the registrant (matched by e-mail); only orders still `pending` or `failed`
    custom       a payment a merchant (or the platform) asks one customer to make: the caller sends the lines and names the customer

Each kind says who may pay, the lines (price per unit, quantity, kind of item for tax), the merchant to be paid, and where the
customer is (for tax). `on_paid` and `on_refunded` then update the order.
"""
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.utils.money import to_decimal

logger = logging.getLogger(__name__)

PURCHASE_TYPES = ("cart_order", "event_order", "custom")


@dataclass
class PayLine:
    description: str
    unit_amount: Decimal
    quantity: int = 1
    category: str = "service"


@dataclass
class Payable:
    purchase_type: str
    purchase_id: str
    tenant_id: UUID | None
    payer_user_id: UUID | None
    payer_email: str | None
    currency: str
    lines: list[PayLine]
    description: str = ""
    prices_include_tax: bool = False
    country: str | None = None
    state: str | None = None
    extra: dict = field(default_factory=dict)


def iso_country(value: str | None) -> str | None:
    """A two-letter code from a code or a country name ('India' -> IN); None when it cannot be told."""
    text = (value or "").strip()
    if not text:
        return None
    if len(text) == 2 and text.isalpha():
        return text.upper()
    try:
        import pycountry

        found = pycountry.countries.get(name=text) or pycountry.countries.get(common_name=text)
        if found is None:
            matches = pycountry.countries.search_fuzzy(text)
            found = matches[0] if matches else None
        return found.alpha_2 if found else None
    except Exception:
        return None


def _forbidden() -> HTTPException:
    return HTTPException(status_code=403, detail="This is not your order")


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} not found")


# ---------------------------------------------------------------- resolve

def resolve(db: Session, actor, body) -> Payable:
    if body.purchase_type == "cart_order":
        return _cart_order(db, actor, body)
    if body.purchase_type == "event_order":
        return _event_order(db, actor, body)
    if body.purchase_type == "custom":
        return _custom(db, actor, body)
    raise HTTPException(status_code=422, detail=f"purchase_type must be one of: {', '.join(PURCHASE_TYPES)}")


def _uuid(value, what: str) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail=f"{what} is not a valid id")


def _cart_order(db: Session, actor, body) -> Payable:
    from app.models.cart_model import Order

    order = db.query(Order).filter(Order.id == _uuid(body.purchase_id, "purchase_id"), Order.is_deleted.is_(False)).first()
    if order is None:
        raise _not_found("Order")
    if str(order.user_id) != actor.id and not actor.is_super_admin:
        raise _forbidden()
    if order.status != "pending":
        raise HTTPException(status_code=409, detail=f"This order is {order.status}, so it cannot be paid")
    lines = [PayLine(i.product_name, to_decimal(i.unit_price), int(i.quantity), "product") for i in order.items]
    if not lines:
        raise HTTPException(status_code=409, detail="This order has no items")
    if abs(sum((l.unit_amount * l.quantity for l in lines), Decimal(0)) - to_decimal(order.total)) > Decimal("0.01"):
        raise HTTPException(status_code=409, detail="The order total does not match its items. Ask support to check it")
    return Payable(
        purchase_type="cart_order", purchase_id=str(order.id), tenant_id=order.tenant_id, payer_user_id=order.user_id, payer_email=actor.email,
        currency=(order.currency or "USD").upper(), lines=lines, description=f"Order {str(order.id)[:8]}",
        country=iso_country(order.shipping_country), state=order.shipping_state,
    )


def _event_order(db: Session, actor, body) -> Payable:
    from app.models.event_aux_models import EventOrder

    order = db.get(EventOrder, _uuid(body.purchase_id, "purchase_id"))
    if order is None:
        raise _not_found("Order")
    if not actor.is_super_admin and (actor.email or "") != (order.participant_email or "").strip().lower():
        raise _forbidden()
    if order.status in ("cancelled", "refunded") or order.payment_status not in ("pending", "failed"):
        raise HTTPException(status_code=409, detail="This order is not waiting for payment")
    event = order.event
    parts = [("Event registration", order.ticket_subtotal), ("Meals", order.meal_subtotal), ("Accommodation", order.accommodation_subtotal)]
    lines = [PayLine(f"{label}: {event.title}" if event else label, to_decimal(value, Decimal(0)), 1, "event") for label, value in parts
             if value is not None and to_decimal(value, Decimal(0)) > 0]
    if not lines:
        lines = [PayLine(f"Registration: {event.title}" if event else "Event registration", to_decimal(order.amount, Decimal(0)), 1, "event")]
    return Payable(
        purchase_type="event_order", purchase_id=str(order.id), tenant_id=event.tenant_id if event else None, payer_user_id=None,
        payer_email=(order.participant_email or "").strip().lower() or actor.email, currency=(order.currency or "INR").upper(), lines=lines,
        description=f"Registration: {event.title}" if event else "Event registration",
    )


def _custom(db: Session, actor, body) -> Payable:
    if actor.role not in ("merchant", "super_admin"):
        raise HTTPException(status_code=403, detail="Only a business or the platform can ask a customer to pay")
    if not body.items:
        raise HTTPException(status_code=422, detail="Send the items to be paid for")
    if body.customer_user_id is None:
        raise HTTPException(status_code=422, detail="customer_user_id is required: who is being asked to pay")
    tenant = actor.tenant_id if actor.role == "merchant" else body.tenant_id
    if tenant is None:
        raise HTTPException(status_code=422, detail="tenant_id is required: which business is paid")
    if not body.item_currency:
        raise HTTPException(status_code=422, detail="item_currency is required: the currency the prices are in")
    lines = [PayLine(i.description or "Item", to_decimal(i.amount), i.quantity, i.category) for i in body.items]
    return Payable(
        purchase_type="custom", purchase_id=body.purchase_id or str(uuid4()), tenant_id=tenant, payer_user_id=body.customer_user_id,
        payer_email=(body.customer_email or "").strip().lower() or None, currency=(body.item_currency or body.currency).upper(), lines=lines,
        description=body.description or "Payment", prices_include_tax=bool(body.prices_include_tax), extra={"created_by": actor.id},
    )


def from_stored(payment) -> Payable:
    """The same payable again for a retry, from what the payment kept (custom payments have no order to read it from)."""
    stored = (payment.extra or {}).get("payable") or {}
    return Payable(
        purchase_type=payment.purchase_type, purchase_id=payment.purchase_id, tenant_id=payment.tenant_id, payer_user_id=payment.customer_user_id,
        payer_email=payment.customer_email, currency=payment.item_currency,
        lines=[PayLine(l["description"], to_decimal(l["unit_amount"]), int(l["quantity"]), l["category"]) for l in stored.get("lines", [])],
        description=stored.get("description", ""), prices_include_tax=bool(stored.get("prices_include_tax")), extra=stored.get("extra") or {},
    )


def to_stored(payable: Payable) -> dict:
    return {"lines": [{"description": l.description, "unit_amount": format(l.unit_amount, "f"), "quantity": l.quantity, "category": l.category} for l in payable.lines],
            "description": payable.description, "prices_include_tax": payable.prices_include_tax, "extra": payable.extra}


# ---------------------------------------------------------------- what paying / refunding does to the order

def on_paid(db: Session, payment) -> None:
    try:
        if payment.purchase_type == "cart_order":
            from app.models.cart_model import Order

            order = db.get(Order, UUID(payment.purchase_id))
            if order is not None and order.status == "pending":
                order.status = "confirmed"
        elif payment.purchase_type == "event_order":
            from app.models.event_aux_models import EventOrder

            order = db.get(EventOrder, UUID(payment.purchase_id))
            if order is not None:
                order.payment_status = "confirmed"
                order.payment_provider = "marketplace"
    except Exception:
        logger.exception("could not update the order after payment %s", payment.id)


def on_refunded(db: Session, payment, fully: bool) -> None:
    if not fully:
        return
    try:
        if payment.purchase_type == "cart_order":
            from app.models.cart_model import Order

            order = db.get(Order, UUID(payment.purchase_id))
            if order is not None:
                order.status = "refunded"
        elif payment.purchase_type == "event_order":
            from app.models.event_aux_models import EventOrder

            order = db.get(EventOrder, UUID(payment.purchase_id))
            if order is not None:
                order.payment_status = "refunded"
                order.status = "refunded"
    except Exception:
        logger.exception("could not update the order after the refund of payment %s", payment.id)
