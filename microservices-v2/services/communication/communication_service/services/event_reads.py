"""Event / training reads the group chats need, copied from the events and learning code (event_repo, event_attendee_service,
event_service, training_repo, training_learner_identity). They run on this service's READ-ONLY replicas of events,
registrations, orders, trainings, enrolments and programs (design D5); nothing here writes those tables."""
from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased, joinedload

from communication_service.models.event_aux_models import EventOrder, EventRegistration
from communication_service.models.event_model import Event
from communication_service.models.training_model import Training
from communication_service.repository.query_utils import apply_soft_delete_filter
from communication_service.utils.event_payments import (
    PAYMENT_FREE,
    PAYMENT_UNPAID,
    derive_payment_status,
    money_to_float,
    parse_money,
    payment_status_sql,
)


def _norm(value) -> str | None:
    if value is None or value == "":
        return None
    return str(value).strip().lower()


def event_owner_tenant_id(event) -> str | None:
    """Owning tenant of an Event.

    ``Event.tenant_id`` when present; legacy rows that predate it fall back to the
    owning Enterprise's tenant. Anything else has no resolvable owner (fail closed).
    """
    tenant_id = _norm(getattr(event, "tenant_id", None))
    if tenant_id:
        return tenant_id
    enterprise = getattr(event, "enterprise", None)
    if enterprise is not None:
        return _norm(getattr(enterprise, "tenant_id", None))
    return None


def get_event_by_id(db: Session, event_id: UUID, include_deleted: bool = False):
    query = db.query(Event).options(joinedload(Event.enterprise)).filter(Event.id == event_id)
    if not include_deleted:
        query = apply_soft_delete_filter(query, Event, include_deleted)
    return query.first()


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


def _payment_expression(event_paid: bool):
    return payment_status_sql(
        EventOrder.id, EventOrder.status, EventOrder.payment_status,
        no_order_label=PAYMENT_UNPAID if event_paid else PAYMENT_FREE,
    )


def _to_float(value) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def _event_is_paid(event) -> bool:
    """A paid event is priced by ``pricing_type``, a top-level ``price`` or any priced ticket type.

    Checking all three matters: events priced only through ``ticket_types`` (or legacy rows whose
    ``pricing_type`` defaulted to "free") must not be treated as free.
    """
    if str(getattr(event, "pricing_type", "") or "").lower() == "paid":
        return True
    if _to_float(getattr(event, "price", None)) > 0:
        return True
    for ticket in getattr(event, "ticket_types", None) or []:
        if isinstance(ticket, dict) and _to_float(ticket.get("price")) > 0:
            return True
    return False


def get_training_by_id(db: Session, tid: UUID, include_deleted=False):
    q = db.query(Training).options(joinedload(Training.enterprise)).filter(Training.id == tid)
    if not include_deleted: q = apply_soft_delete_filter(q, Training, include_deleted)
    return q.first()


def _tenant_of(db: Session, training) -> UUID | None:
    tenant = getattr(training, "tenant_id", None)
    if not tenant and getattr(training, "enterprise_id", None):
        from communication_service.models.enterprise_model import Enterprise  # read-only replica

        enterprise = db.query(Enterprise).filter(Enterprise.id == training.enterprise_id).first()
        tenant = getattr(enterprise, "tenant_id", None)
    return _as_uuid(tenant)

