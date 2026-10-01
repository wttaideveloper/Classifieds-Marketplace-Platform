"""Payment-status derivation and money parsing for Event attendee management + the Event dashboard.

There is deliberately ONE definition of "what state is this order in", used by the attendee list
(filtering + display), the dashboard order counts, and the revenue calculation, so the three can never
disagree.

Order state model (verified from every write in event_service.py):

    EventOrder.status          confirmed | completed | cancelled | refund_requested | refunded
    EventOrder.payment_status  confirmed | refund_requested | refunded          (pending / failed are
                                                                                  only documented, never written;
                                                                                  a cancelled order can keep a
                                                                                  stale refund_requested here)

EventOrder has no registration foreign key; a registration is paired with its order by event + participant
email, taking the latest order created at or before the registration (a checkout creates the order first,
then the registration, in one transaction). ``uq_event_reg_active`` guarantees at most one active
registration per event+email, so the pairing is unambiguous for any registration that is still active.

``EventOrder.amount`` is the order TOTAL (unit price x quantity is applied at checkout) stored as a string.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import sqlalchemy as sa

# Derived vocabulary (the API-facing payment_status of an attendee / order).
PAYMENT_PAID = "paid"
PAYMENT_PENDING = "pending"
PAYMENT_REFUND_REQUESTED = "refund_requested"
PAYMENT_REFUNDED = "refunded"
PAYMENT_CANCELLED = "cancelled"
PAYMENT_FAILED = "failed"
PAYMENT_FREE = "free"  # free event, no order
PAYMENT_UNPAID = "unpaid"  # paid event, no order recorded

_SUCCESS_ORDER_STATUSES = ("confirmed", "completed")


def derive_payment_status(order_status: Any, order_payment_status: Any) -> str:
    """Derived payment state of ONE existing order. Precedence: refunded > cancelled > refund_requested >
    failed > pending > paid (case-insensitive); anything unrecognised is treated as not-yet-paid ("pending"),
    never as paid.

    A ``cancelled`` order stays cancelled even when its ``payment_status`` still says ``refund_requested``: an
    admin can move ``refund_requested -> cancelled`` without touching the payment column, and that closed
    order must not read as a refund that is still pending."""
    status = str(order_status or "").lower()
    payment = str(order_payment_status or "").lower()
    if status == "refunded" or payment == "refunded":
        return PAYMENT_REFUNDED
    if status == "cancelled":
        return PAYMENT_CANCELLED
    if status == "refund_requested" or payment == "refund_requested":
        return PAYMENT_REFUND_REQUESTED
    if payment == "failed":
        return PAYMENT_FAILED
    if payment == "pending":
        return PAYMENT_PENDING
    if payment == "confirmed" and status in _SUCCESS_ORDER_STATUSES:
        return PAYMENT_PAID
    return PAYMENT_PENDING


def payment_status_sql(order_id_col, order_status_col, order_payment_col, *, no_order_label: str):
    """SQL ``CASE`` equivalent of :func:`derive_payment_status`, for filtering in the database.

    ``no_order_label`` is what a registration without any order means: ``free`` for a free event,
    ``unpaid`` for a paid one. Kept in lock-step with the Python function by a truth-table test.
    """
    status = sa.func.lower(sa.func.coalesce(order_status_col, ""))
    payment = sa.func.lower(sa.func.coalesce(order_payment_col, ""))
    return sa.case(
        (order_id_col.is_(None), no_order_label),
        (sa.or_(status == "refunded", payment == "refunded"), PAYMENT_REFUNDED),
        (status == "cancelled", PAYMENT_CANCELLED),
        (sa.or_(status == "refund_requested", payment == "refund_requested"), PAYMENT_REFUND_REQUESTED),
        (payment == "failed", PAYMENT_FAILED),
        (payment == "pending", PAYMENT_PENDING),
        (sa.and_(payment == "confirmed", status.in_(_SUCCESS_ORDER_STATUSES)), PAYMENT_PAID),
        else_=PAYMENT_PENDING,
    )


def parse_money(value: Any) -> Decimal | None:
    """An order amount string as a finite Decimal, or None when it cannot be trusted (never raises)."""
    if value is None:
        return None
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() else None


def money_to_float(amount: Decimal) -> float:
    """Two-decimal float for JSON. Sums are accumulated as Decimal and rounded once, here."""
    return float(amount.quantize(Decimal("0.01")))
