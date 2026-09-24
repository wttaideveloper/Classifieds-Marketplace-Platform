"""Event dashboard (Phase 2.3): one read-only, tenant-scoped snapshot of an event's operational state.

Every number comes from SQL aggregation (constant number of statements, whatever the event's size):

  registrations / attendance -> EventRegistration.status, grouped
  capacity                   -> the SAME helper the registration/checkout/waitlist paths use
                                (event_service._seats_taken + the live-offer rule of _try_promote_from_waitlist)
  waitlist                   -> EventWaitlist.status, grouped
  orders / revenue           -> EventOrder only, grouped by (currency, status, payment_status, ticket type, amount)
                                and summed with Decimal. Ticket prices and registration counts are never used.

Callers are responsible for authorization (the route uses the Phase 2.1 event-ownership dependency).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Iterable
from uuid import UUID

import sqlalchemy as sa
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.event_aux_models import EventOrder, EventRegistration, EventWaitlist
from app.repository.event_repo import get_event_by_id
from app.schemas.event_management_schema import (
    DashboardAttendance,
    DashboardCapacity,
    DashboardEvent,
    DashboardOrders,
    DashboardRegistrations,
    DashboardRevenue,
    DashboardWaitlist,
    EventDashboardResponse,
    RevenueLine,
)
from app.schemas.event_schema import EventModules
from app.services.event_service import _seats_taken
from app.utils.event_modules import resolve_event_modules, resolve_event_type
from app.utils.event_payments import (
    PAYMENT_CANCELLED,
    PAYMENT_FAILED,
    PAYMENT_PAID,
    PAYMENT_PENDING,
    PAYMENT_REFUND_REQUESTED,
    PAYMENT_REFUNDED,
    derive_payment_status,
    money_to_float,
    parse_money,
)
from app.utils.event_utils import get_event_lifecycle_state

_ZERO = Decimal("0")
_UNSPECIFIED_TICKET = "unspecified"


def _percentage(part: int, whole: int) -> float | None:
    return round(part / whole * 100, 1) if whole > 0 else None


def _parse_capacity(raw) -> int | None:
    """Same reading as the seat-taking paths (``int(float(str(capacity)))``); unusable -> unlimited."""
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return max(int(float(str(raw).strip())), 0)
    except (ValueError, OverflowError):
        return None


# ---------------------------------------------------------------------------------------------
# orders + revenue (shared with the legacy ``type=revenue`` report)
# ---------------------------------------------------------------------------------------------


def _order_groups(db: Session, event_id: UUID) -> list:
    """Every order of the event, collapsed to distinct (currency, status, payment status, ticket, amount)
    with a count. ``EventOrder.amount`` is a string, so it is grouped as text and summed as Decimal in
    Python — no database-side cast that could fail on a malformed value."""
    return db.execute(
        sa.select(
            EventOrder.currency, EventOrder.status, EventOrder.payment_status, EventOrder.ticket_type_id,
            EventOrder.amount, sa.func.count(EventOrder.id),
        )
        .where(EventOrder.event_id == event_id)
        .group_by(
            EventOrder.currency, EventOrder.status, EventOrder.payment_status, EventOrder.ticket_type_id, EventOrder.amount
        )
    ).all()


class _OrderSummary:
    def __init__(self, groups: Iterable) -> None:
        self.counts: dict[str, int] = defaultdict(int)
        self.unparseable = 0
        # currency -> derived payment status -> [amount, orders]
        self._money: dict[str | None, dict[str, list]] = defaultdict(lambda: defaultdict(lambda: [_ZERO, 0]))
        # ticket type -> paid amount, per currency
        self._paid_by_ticket: dict[str | None, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: _ZERO))
        for currency, order_status, order_payment, ticket_type_id, amount, n in groups:
            derived = derive_payment_status(order_status, order_payment)
            self.counts[derived] += n
            money = parse_money(amount)
            if money is None:
                self.unparseable += n
                continue
            key = (str(currency).strip().upper() or None) if currency is not None else None
            bucket = self._money[key][derived]
            bucket[0] += money * n
            bucket[1] += n
            if derived == PAYMENT_PAID:
                self._paid_by_ticket[key][ticket_type_id or _UNSPECIFIED_TICKET] += money * n

    @property
    def total_orders(self) -> int:
        return sum(self.counts.values())

    def lines(self) -> list[RevenueLine]:
        """One line per currency that has any revenue-relevant order (paid / refund requested / refunded)."""
        lines = []
        for currency in sorted(self._money, key=lambda c: c or ""):
            by_status = self._money[currency]
            if not any(by_status[s][1] for s in (PAYMENT_PAID, PAYMENT_REFUND_REQUESTED, PAYMENT_REFUNDED) if s in by_status):
                continue
            lines.append(
                RevenueLine(
                    currency=currency,
                    total_revenue=money_to_float(by_status[PAYMENT_PAID][0] if PAYMENT_PAID in by_status else _ZERO),
                    refunded_amount=money_to_float(by_status[PAYMENT_REFUNDED][0] if PAYMENT_REFUNDED in by_status else _ZERO),
                    pending_refund_amount=money_to_float(
                        by_status[PAYMENT_REFUND_REQUESTED][0] if PAYMENT_REFUND_REQUESTED in by_status else _ZERO
                    ),
                    paid_orders=by_status[PAYMENT_PAID][1] if PAYMENT_PAID in by_status else 0,
                )
            )
        return lines

    def paid_by_ticket(self, currency: str | None) -> dict[str, float]:
        return {
            ticket: money_to_float(total)
            for ticket, total in sorted(self._paid_by_ticket.get(currency, {}).items())
        }


def _revenue(summary: _OrderSummary, event_currency: str | None) -> DashboardRevenue:
    lines = summary.lines()
    revenue = DashboardRevenue(by_currency=lines, unparseable_orders=summary.unparseable, mixed_currency=len(lines) > 1)
    if len(lines) > 1:
        return revenue  # a total across currencies would be meaningless: scalar fields stay null
    if lines:
        line = lines[0]
        revenue.currency = line.currency
        revenue.total_revenue = line.total_revenue
        revenue.refunded_amount = line.refunded_amount
        revenue.pending_refund_amount = line.pending_refund_amount
        revenue.paid_orders = line.paid_orders
    else:
        revenue.currency = event_currency
        revenue.total_revenue = revenue.refunded_amount = revenue.pending_refund_amount = 0.0
    return revenue


def compute_revenue_report(db: Session, event_id: UUID, event_currency: str | None) -> dict:
    """Payload of the legacy ``GET /events/{id}/reports?type=revenue``, now sourced from EventOrder.

    Keeps that report's shape (``total_revenue``, ``by_ticket_type``, ``currency``) and adds
    ``mixed_currency`` / ``by_currency``. With more than one currency the headline total is the event's
    own currency only — never a sum across currencies.
    """
    summary = _OrderSummary(_order_groups(db, event_id))
    lines = summary.lines()
    wanted = (event_currency or "").strip().upper() or None
    line = next((l for l in lines if l.currency == wanted), None)
    if line is None and len(lines) == 1:
        line = lines[0]
    return {
        "total_revenue": line.total_revenue if line else 0.0,
        "by_ticket_type": summary.paid_by_ticket(line.currency) if line else {},
        "currency": line.currency if line and line.currency else event_currency,
        "mixed_currency": len(lines) > 1,
        "by_currency": [l.model_dump() for l in lines],
    }


# ---------------------------------------------------------------------------------------------
# the dashboard
# ---------------------------------------------------------------------------------------------


def _registrations_block(db: Session, event_id: UUID) -> tuple[DashboardRegistrations, DashboardAttendance]:
    by_status: dict[str | None, int] = dict(
        db.execute(
            sa.select(EventRegistration.status, sa.func.count(EventRegistration.id))
            .where(EventRegistration.event_id == event_id)
            .group_by(EventRegistration.status)
        ).all()
    )
    confirmed = by_status.get("confirmed", 0)
    attended = by_status.get("attended", 0)
    cancelled = by_status.get("cancelled", 0)
    no_show = by_status.get("no_show", 0)
    total = sum(by_status.values())
    registrations = DashboardRegistrations(
        total=total,
        active=confirmed + attended,
        confirmed=confirmed,
        attended=attended,
        cancelled=cancelled,
        no_show=no_show,
        other=total - confirmed - attended - cancelled - no_show,
    )
    attendance = DashboardAttendance(
        checked_in=attended,
        not_checked_in=confirmed,
        attendance_percentage=_percentage(attended, confirmed + attended),
    )
    return registrations, attendance


def _waitlist_block(db: Session, event_id: UUID) -> tuple[DashboardWaitlist, int]:
    """Waitlist counts by status, plus the number of LIVE payment offers (the seats they hold).

    An offer is live while ``payment_pending`` and not past ``payment_offer_expires_at`` — the exact rule
    ``_try_promote_from_waitlist`` uses to decide whether a seat is still promised.
    """
    live = sa.and_(
        EventWaitlist.status == "payment_pending",
        sa.or_(EventWaitlist.payment_offer_expires_at.is_(None), EventWaitlist.payment_offer_expires_at > datetime.utcnow()),
    )
    rows = db.execute(
        sa.select(EventWaitlist.status, sa.func.count(EventWaitlist.id), sa.func.sum(sa.case((live, 1), else_=0)))
        .where(EventWaitlist.event_id == event_id)
        .group_by(EventWaitlist.status)
    ).all()
    by_status = {row[0]: row[1] for row in rows}
    live_offers = int(sum(row[2] or 0 for row in rows))
    known = ("waiting", "payment_pending", "promoted", "expired", "left")
    total = sum(by_status.values())
    waitlist = DashboardWaitlist(
        total=total,
        waiting=by_status.get("waiting", 0),
        payment_pending=by_status.get("payment_pending", 0),
        promoted=by_status.get("promoted", 0),
        expired=by_status.get("expired", 0),
        left=by_status.get("left", 0),
        other=total - sum(by_status.get(s, 0) for s in known),
    )
    return waitlist, live_offers


def _capacity_block(db: Session, event, live_offers: int) -> DashboardCapacity:
    capacity = _parse_capacity(getattr(event, "capacity", None))
    seats_taken = _seats_taken(db, event.id)
    if capacity is None:
        return DashboardCapacity(
            capacity=None, unlimited=True, seats_taken=seats_taken, seats_reserved=live_offers,
            available_seats=None, is_full=None, fill_percentage=None,
        )
    available = max(capacity - seats_taken - live_offers, 0)
    return DashboardCapacity(
        capacity=capacity,
        unlimited=False,
        seats_taken=seats_taken,
        seats_reserved=live_offers,
        available_seats=available,
        is_full=available == 0,
        fill_percentage=_percentage(seats_taken, capacity),
    )


def get_event_dashboard_service(db: Session, event_id: UUID) -> EventDashboardResponse:
    event = get_event_by_id(db, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    registrations, attendance = _registrations_block(db, event_id)
    waitlist, live_offers = _waitlist_block(db, event_id)
    capacity = _capacity_block(db, event, live_offers)
    summary = _OrderSummary(_order_groups(db, event_id))
    orders = DashboardOrders(
        total=summary.total_orders,
        successful=summary.counts[PAYMENT_PAID],
        pending=summary.counts[PAYMENT_PENDING],
        refund_requested=summary.counts[PAYMENT_REFUND_REQUESTED],
        refunded=summary.counts[PAYMENT_REFUNDED],
        cancelled=summary.counts[PAYMENT_CANCELLED],
        failed=summary.counts[PAYMENT_FAILED],
    )
    return EventDashboardResponse(
        event=DashboardEvent(
            id=event.id,
            title=event.title,
            status=event.status,
            lifecycle_state=get_event_lifecycle_state(event),
            event_type=resolve_event_type(event),
            modules=EventModules(**resolve_event_modules(event)),
            pricing_type=event.pricing_type,
            currency=event.currency,
            start_date=event.start_date,
            end_date=event.end_date,
            time_zone=event.time_zone,
        ),
        registrations=registrations,
        capacity=capacity,
        attendance=attendance,
        waitlist=waitlist,
        orders=orders,
        revenue=_revenue(summary, event.currency),
        generated_at=datetime.utcnow(),
    )
