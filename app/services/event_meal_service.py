"""Meals for an Event (Phase 2.6): selection validation shared by every registration path, the attendee/organizer
meal update, and the per-meal counts.

Configuration rules (options, stable ids, enabled) live in ``app/utils/event_meals.py``; ``modules.meals`` is the only
"meals on" switch. This module adds the parts that need a database or an HTTP error:

* ``validated_selections``: the ONE validator used by online registration, walk-in registration and the update
  endpoint, so there is a single set of rules;
* ``update_registration_meals_service``: replace one registration's selections, by the participant themself or by
  the owning organizer (Phase 2.1 ownership);
* ``summarize_meals``: how many active registrations selected each option, from one grouped SQL statement
  (JSON array expansion in the database; the registrations are never loaded into Python).
"""
from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.models.event_aux_models import EventRegistration
from app.repository.event_repo import assert_event_access
from app.schemas.event_meal_schema import AttendeeMealSelection, DashboardMeal, EventMealSelectionResponse
from app.services.event_service import _actor_id, _get_event_or_404, _log_audit
from app.utils.event_meals import (
    MealSelectionError,
    meals_enabled,
    selection_views,
    stored_options,
    validate_meal_selections,
)

_ACTIVE_STATUSES = ("confirmed", "attended")
# Events whose registrations are closed (online registration / checkout / walk-in use the same set).
_CLOSED_EVENT_STATUSES = ("cancelled", "completed", "archived", "suspended")


def validated_selections(event, selections, current=None) -> list[str] | None:
    """Validate a meal selection against the event's configuration (422 with a clear message), returning the
    normalised list to store (None = none selected). ``current`` = what the registration already holds."""
    try:
        return validate_meal_selections(event, selections, current)
    except MealSelectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def attendee_meal_selections(options: list[dict], selected) -> list[AttendeeMealSelection]:
    return [AttendeeMealSelection(**view) for view in selection_views(options, selected)]


# ---------------------------------------------------------------------------------------------
# update a registration's meals
# ---------------------------------------------------------------------------------------------


def _is_participant(reg, current_user: dict | None) -> bool:
    email = ((current_user or {}).get("email") or "").strip().lower()
    return bool(email) and (reg.participant_email or "").strip().lower() == email


def update_registration_meals_service(
    db: Session, event_id: UUID, reg_id: UUID, payload, current_user: dict | None, access_token: str | None = None
) -> EventMealSelectionResponse:
    """Replace the meal selections of ONE registration of THIS event.

    Allowed: the participant (email matches the registration's, case-insensitively, as for cancellation and the QR) or
    the event's owner (admin/provider of the owning tenant, or an active platform super admin). A registration id alone
    grants nothing. Only an active registration of an open event can change its meals. An organizer's change is audited;
    a participant changing their own preference is not (no audit noise), like other self-service changes.
    """
    event = _get_event_or_404(db, event_id)
    reg = db.execute(
        sa.select(EventRegistration).where(EventRegistration.id == reg_id, EventRegistration.event_id == event_id)
    ).scalar_one_or_none()
    if reg is None:
        raise HTTPException(status_code=404, detail="Registration not found")
    participant = _is_participant(reg, current_user)
    if not participant:
        assert_event_access(db, event, current_user, access_token=access_token)  # 403 unless the owner
    if event.status in _CLOSED_EVENT_STATUSES:
        raise HTTPException(status_code=400, detail=f"Meal selections are closed — event is {event.status}")
    if reg.status not in _ACTIVE_STATUSES:
        raise HTTPException(status_code=400, detail=f"Cannot update meals: registration is {reg.status}")

    before = list(reg.meal_selections) if isinstance(reg.meal_selections, list) else []
    selections = validated_selections(event, payload.meal_selections, current=before)
    reg.meal_selections = selections  # a new list (or None), so the JSONB change is always detected
    if not participant:
        _log_audit(
            db, event_id, "meal_selection_update",
            {"registration_id": str(reg.id), "meal_selections": before},
            {"registration_id": str(reg.id), "participant_email": reg.participant_email, "meal_selections": selections or []},
            changed_by=_actor_id(current_user), commit=False,
        )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return EventMealSelectionResponse(
        event_id=event_id, registration_id=reg.id,
        meal_selections=attendee_meal_selections(stored_options(event), reg.meal_selections),
    )


# ---------------------------------------------------------------------------------------------
# counts
# ---------------------------------------------------------------------------------------------


def selected_meal_counts(db: Session, event_id: UUID) -> dict[str, int]:
    """meal option id -> number of ACTIVE registrations (confirmed/attended) that selected it, in one statement.

    The JSON array is expanded in the database (``jsonb_array_elements_text`` on PostgreSQL, ``json_each`` on SQLite,
    used by the local test database) and grouped there. A column that is not a JSON array counts as empty rather than
    failing the query.
    """
    column = EventRegistration.meal_selections
    if db.get_bind().dialect.name == "postgresql":
        array = sa.case((sa.func.jsonb_typeof(column) == "array", column), else_=sa.cast(sa.literal("[]"), JSONB))
        # ``AS alias(value)``: name the output column explicitly rather than rely on how PostgreSQL names a scalar SRF's column.
        elements = sa.func.jsonb_array_elements_text(array).table_valued("value").render_derived()
    else:
        array = sa.case((sa.func.json_type(column) == "array", column), else_=sa.literal("[]"))
        elements = sa.func.json_each(array).table_valued("value")
    statement = (
        sa.select(elements.c.value, sa.func.count())
        .select_from(EventRegistration)
        .join(elements, sa.true())
        .where(EventRegistration.event_id == event_id, EventRegistration.status.in_(_ACTIVE_STATUSES))
        .group_by(elements.c.value)
    )
    return {value: count for value, count in db.execute(statement).all()}


def summarize_meals(db: Session, event) -> list[DashboardMeal]:
    """The dashboard's meal section: one row per option of an event that has meals ON. Empty otherwise (meals off, or no
    options), and no query at all in those cases. Retired options appear only while someone still holds them."""
    options = stored_options(event)
    if not options or not meals_enabled(event):
        return []
    counts = selected_meal_counts(db, event.id)
    return [
        DashboardMeal(meal_id=option["id"], name=option["name"], selected_count=counts.get(option["id"], 0), active=option["active"])
        for option in options
        if option["active"] or counts.get(option["id"], 0) > 0
    ]
