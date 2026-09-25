"""Attendee management for an Event (Phase 2.3): search, filters, pagination, detail and CSV export.

EventRegistration stays the source of attendee information — there is no attendee table. What an
attendee *paid* is derived from the paired EventOrder (see app/utils/event_payments.py); nothing is
duplicated onto the registration.

Query strategy (no N+1): every registration row is fetched together with its paired order in ONE
statement, by left-joining EventOrder on a correlated "latest order for this event + email created at or
before the registration" subquery. Filtering, sorting and paging all happen in the database; the number
of statements per request is constant regardless of how many attendees the event has.

Callers are responsible for authorization (the routes use the Phase 2.1 event-ownership dependency).
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Iterator
from uuid import UUID

import sqlalchemy as sa
from fastapi import HTTPException, status
from sqlalchemy.orm import Session, aliased

from app.models.event_aux_models import EventOrder, EventRegistration
from app.models.event_form_config_model import EventFormConfigurationVersion
from app.repository.event_repo import get_event_by_id
from app.repository.query_utils import build_pagination_meta
from app.schemas.event_management_schema import (
    EventAttendeeAnswer,
    EventAttendeePaginatedResponse,
    EventAttendeeResponse,
)
from app.services.event_form_registry import LEGACY_VERSION_ID
from app.services.event_meal_service import attendee_meal_selections
from app.services.event_service import _event_is_paid, _to_int
from app.services.event_session_attendance_service import event_sessions_with_ids, load_attendance_rows, session_states
from app.utils.event_meals import stored_options
from app.utils.event_payments import (
    PAYMENT_FREE,
    PAYMENT_UNPAID,
    derive_payment_status,
    money_to_float,
    parse_money,
    payment_status_sql,
)

# Keys the registration flow stores next to real answers; they are bookkeeping, not answers.
_RESERVED_ANSWER_KEYS = frozenset({"group_members", "group_size", "group_leader"})
_EXPORT_BATCH_SIZE = 500
# A spreadsheet treats a cell starting with one of these as a formula (CSV injection).
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


@dataclass
class AttendeeFilters:
    """Query filters shared by the attendee list and the CSV export."""

    q: str | None = None
    status: str | None = None
    ticket_type_id: str | None = None
    payment_status: str | None = None
    checked_in: bool | None = None
    source: str | None = None
    registered_from: date | None = None
    registered_to: date | None = None
    sort: str = "newest"


@dataclass
class _EventContext:
    event: Any
    paid: bool
    ticket_names: dict[str, str]
    answer_labels: dict[str, str]
    sessions: list[dict]
    meal_options: list[dict]


# ---------------------------------------------------------------------------------------------
# building blocks
# ---------------------------------------------------------------------------------------------


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _linked_orders(event_id: UUID):
    """Subquery ``(registration_id, order_id)``: the order that paid for each registration of the event.

    Same pairing the Phase 2.1 refund/check-in code uses (event + email, latest order not created after the
    registration; a checkout always creates the order first). Written as a ranked join, not a per-row
    correlated subquery, so the database can hash-join the event's registrations and orders once — cost
    grows with (registrations + orders), not their product.
    """
    reg = aliased(EventRegistration)
    order = aliased(EventOrder)
    ranked = (
        sa.select(
            reg.id.label("registration_id"),
            order.id.label("order_id"),
            sa.func.row_number()
            .over(partition_by=reg.id, order_by=(order.created_at.desc(), order.id.desc()))
            .label("rank"),
        )
        .select_from(reg)
        .outerjoin(
            order,
            sa.and_(
                order.event_id == reg.event_id,
                sa.func.lower(order.participant_email) == sa.func.lower(reg.participant_email),
                order.created_at <= reg.created_at,
            ),
        )
        .where(reg.event_id == event_id)
        .subquery()
    )
    return sa.select(ranked.c.registration_id, ranked.c.order_id).where(ranked.c.rank == 1).subquery()


def _with_orders(statement, event_id: UUID):
    linked = _linked_orders(event_id)
    return statement.outerjoin(linked, linked.c.registration_id == EventRegistration.id).outerjoin(
        EventOrder, EventOrder.id == linked.c.order_id
    )


def _rows_statement(event_id: UUID):
    return _with_orders(sa.select(EventRegistration, EventOrder).select_from(EventRegistration), event_id)


def _payment_expression(event_paid: bool):
    return payment_status_sql(
        EventOrder.id, EventOrder.status, EventOrder.payment_status,
        no_order_label=PAYMENT_UNPAID if event_paid else PAYMENT_FREE,
    )


def _conditions(event_id: UUID, filters: AttendeeFilters, event_paid: bool) -> list:
    conditions = [EventRegistration.event_id == event_id]
    term = (filters.q or "").strip()
    if term:
        pattern = f"%{_escape_like(term)}%"
        clauses = [
            EventRegistration.participant_name.ilike(pattern, escape="\\"),
            EventRegistration.participant_email.ilike(pattern, escape="\\"),
            EventRegistration.qr_code.ilike(pattern, escape="\\"),
        ]
        try:
            clauses.append(EventRegistration.id == UUID(term))  # pasting a registration id finds it exactly
        except ValueError:
            pass
        conditions.append(sa.or_(*clauses))
    if filters.status:
        conditions.append(EventRegistration.status == filters.status)
    if filters.ticket_type_id:
        conditions.append(EventRegistration.ticket_type_id == filters.ticket_type_id)
    if filters.checked_in is True:
        conditions.append(EventRegistration.status == "attended")
    elif filters.checked_in is False:
        conditions.append(sa.or_(EventRegistration.status.is_(None), EventRegistration.status != "attended"))
    if filters.source == "walk_in":
        conditions.append(EventRegistration.registration_source == "walk_in")
    elif filters.source == "online":  # NULL (every row that predates walk-ins) is online
        conditions.append(sa.or_(EventRegistration.registration_source.is_(None), EventRegistration.registration_source != "walk_in"))
    if filters.registered_from:
        conditions.append(EventRegistration.created_at >= datetime.combine(filters.registered_from, time.min))
    if filters.registered_to:
        conditions.append(EventRegistration.created_at < datetime.combine(filters.registered_to + timedelta(days=1), time.min))
    if filters.payment_status:
        conditions.append(_payment_expression(event_paid) == filters.payment_status)
    return conditions


def _order_by(sort: str) -> list:
    """Deterministic: every ordering ends in the primary key, so pages never repeat or skip rows."""
    if sort == "oldest":
        return [EventRegistration.created_at.asc(), EventRegistration.id.asc()]
    if sort == "name":
        return [sa.func.lower(EventRegistration.participant_name).asc(), EventRegistration.id.asc()]
    if sort == "email":
        return [sa.func.lower(EventRegistration.participant_email).asc(), EventRegistration.id.asc()]
    return [EventRegistration.created_at.desc(), EventRegistration.id.desc()]


def validate_filters(filters: AttendeeFilters) -> None:
    if filters.registered_from and filters.registered_to and filters.registered_from > filters.registered_to:
        raise HTTPException(status_code=422, detail="registered_from must be on or before registered_to")


def _load_event(db: Session, event_id: UUID):
    event = get_event_by_id(db, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return event


def load_answer_labels(db: Session, event) -> dict[str, str]:
    """field id -> question label for the event's registration form (one query; empty if unavailable).

    Reuses the Event Form infrastructure: the event's pinned form version, or — like the existing
    ``GET /events/{id}/form-configuration`` — the legacy default version. Mobile keys every registration
    answer by that version's field id. The raw sections are read (not the normalised, enabled-only view) so
    an answer to a field that was disabled later still shows its question.
    """
    version_id = getattr(event, "form_configuration_version_id", None) or UUID(LEGACY_VERSION_ID)
    version = db.get(EventFormConfigurationVersion, version_id)
    if version is None or not isinstance(version.sections, list):
        return {}
    labels: dict[str, str] = {}
    for section in version.sections:
        for field in (section.get("fields") if isinstance(section, dict) else None) or []:
            if isinstance(field, dict) and field.get("id") is not None and field.get("label"):
                labels[str(field["id"])] = str(field["label"])
    return labels


def _context(db: Session, event) -> _EventContext:
    return _EventContext(
        event=event,
        paid=_event_is_paid(event),
        ticket_names={str(t.get("id")): t.get("name") for t in (event.ticket_types or []) if isinstance(t, dict) and t.get("id") is not None},
        answer_labels=load_answer_labels(db, event),
        sessions=event_sessions_with_ids(event),
        meal_options=stored_options(event),
    )


def _answers(raw: Any, labels: dict[str, str]) -> list[EventAttendeeAnswer]:
    if not isinstance(raw, dict):
        return []
    position = {field_id: index for index, field_id in enumerate(labels)}
    items = [(str(key), value) for key, value in raw.items() if key not in _RESERVED_ANSWER_KEYS]
    items.sort(key=lambda item: (position.get(item[0], len(position)), item[0]))
    return [EventAttendeeAnswer(field_id=key, label=labels.get(key) or key, value=value) for key, value in items]


def _attendee(reg, order, ctx: _EventContext, session_rows: dict | None = None) -> EventAttendeeResponse:
    """``session_rows`` (session id -> attendance row) is passed by the list/detail; the export leaves it out."""
    if order is not None:
        payment = derive_payment_status(order.status, order.payment_status)
        amount = parse_money(order.amount)
        quantity = max(_to_int(order.quantity, 1), 1)
    else:
        payment = PAYMENT_UNPAID if ctx.paid else PAYMENT_FREE
        amount, quantity = None, 1
    return EventAttendeeResponse(
        registration_id=reg.id,
        event_id=reg.event_id,
        participant_name=reg.participant_name,
        participant_email=reg.participant_email,
        registration_status=reg.status,
        registration_reference=reg.qr_code,
        ticket_type_id=reg.ticket_type_id,
        ticket_type_name=ctx.ticket_names.get(str(reg.ticket_type_id)) if reg.ticket_type_id else None,
        quantity=quantity,
        payment_status=payment,
        order_id=order.id if order is not None else None,
        order_status=order.status if order is not None else None,
        amount=money_to_float(amount) if amount is not None else None,
        currency=order.currency if order is not None else None,
        is_checked_in=reg.status == "attended",
        checked_in_at=reg.checked_in_at,
        checked_out_at=reg.checked_out_at,
        registered_at=reg.created_at,
        registration_source="walk_in" if reg.registration_source == "walk_in" else "online",
        meal_selections=attendee_meal_selections(ctx.meal_options, reg.meal_selections),
        custom_answers=_answers(reg.custom_fields, ctx.answer_labels),
        session_attendance=session_states(ctx.sessions, session_rows) if session_rows is not None else [],
    )


# ---------------------------------------------------------------------------------------------
# list / detail
# ---------------------------------------------------------------------------------------------


def list_attendees_service(db: Session, event_id: UUID, filters: AttendeeFilters, page: int, page_size: int) -> EventAttendeePaginatedResponse:
    validate_filters(filters)
    event = _load_event(db, event_id)
    ctx = _context(db, event)
    conditions = _conditions(event_id, filters, ctx.paid)

    count_statement = sa.select(sa.func.count(EventRegistration.id)).select_from(EventRegistration)
    if filters.payment_status:  # only the payment filter needs the order join
        count_statement = _with_orders(count_statement, event_id)
    total = db.execute(count_statement.where(*conditions)).scalar_one()

    rows = db.execute(
        _rows_statement(event_id)
        .where(*conditions)
        .order_by(*_order_by(filters.sort))
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    attendance = load_attendance_rows(db, event_id, [reg.id for reg, _ in rows]) if ctx.sessions else {}
    return EventAttendeePaginatedResponse(
        items=[_attendee(reg, order, ctx, attendance.get(reg.id, {})) for reg, order in rows],
        pagination=build_pagination_meta(total, page, page_size),
    )


def get_attendee_service(db: Session, event_id: UUID, registration_id: UUID) -> EventAttendeeResponse:
    """One registration of THIS event. A registration of another event is a 404, never a leak."""
    event = _load_event(db, event_id)
    row = db.execute(
        _rows_statement(event_id).where(EventRegistration.id == registration_id, EventRegistration.event_id == event_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registration not found")
    reg, order = row
    ctx = _context(db, event)
    attendance = load_attendance_rows(db, event_id, [reg.id]) if ctx.sessions else {}
    return _attendee(reg, order, ctx, attendance.get(reg.id, {}))


# ---------------------------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------------------------


def _csv_text(value: Any) -> str:
    """Text cell, neutralised against spreadsheet formula injection (names/answers are user-supplied)."""
    if value is None:
        return ""
    text = str(value)
    return "'" + text if text.startswith(_CSV_FORMULA_PREFIXES) else text


def _answer_text(value: Any) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return "; ".join(str(item) for item in value)
    return "" if value is None else str(value)


def _iter_rows(db: Session, event_id: UUID, filters: AttendeeFilters, ctx: _EventContext) -> Iterator[tuple]:
    statement = (
        _rows_statement(event_id)
        .where(*_conditions(event_id, filters, ctx.paid))
        .order_by(*_order_by(filters.sort))
        .execution_options(yield_per=_EXPORT_BATCH_SIZE)  # batches, so a huge event is never fully in memory as ORM objects
    )
    yield from db.execute(statement)


def export_attendees_csv(db: Session, event_id: UUID, filters: AttendeeFilters) -> str:
    """CSV of the attendees matching ``filters``.

    The first five columns keep their original names and order (id, name, email, status, qr_code) so an
    existing consumer keeps working; everything else is appended. All free text is neutralised against
    spreadsheet formula injection.
    """
    validate_filters(filters)
    event = _load_event(db, event_id)
    ctx = _context(db, event)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "name", "email", "status", "qr_code",
        "ticket_type", "quantity", "payment_status", "order_id", "amount", "currency",
        "checked_in", "checked_in_at", "registered_at", "answers", "source", "meals",
    ])
    for reg, order in _iter_rows(db, event_id, filters, ctx):
        attendee = _attendee(reg, order, ctx)
        writer.writerow([
            attendee.registration_id,
            _csv_text(attendee.participant_name),
            _csv_text(attendee.participant_email),
            attendee.registration_status,
            _csv_text(attendee.registration_reference),
            _csv_text(attendee.ticket_type_name or attendee.ticket_type_id),
            attendee.quantity,
            attendee.payment_status,
            attendee.order_id or "",
            "" if attendee.amount is None else attendee.amount,
            _csv_text(attendee.currency),
            "yes" if attendee.is_checked_in else "no",
            attendee.checked_in_at.isoformat() if attendee.checked_in_at else "",
            attendee.registered_at.isoformat() if attendee.registered_at else "",
            _csv_text("; ".join(f"{a.label}: {_answer_text(a.value)}" for a in attendee.custom_answers)),
            attendee.registration_source,
            _csv_text("; ".join(selection.name or selection.meal_id for selection in attendee.meal_selections)),
        ])
    return output.getvalue()
