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

from app.models.event_aux_models import EventRegistration, EventRegistrationOption
from app.repository.event_repo import assert_event_access
from app.schemas.event_meal_schema import AttendeeMealSelection, DashboardMeal, EventMealSelectionResponse
from app.services.event_service import _actor_id, _get_event_or_404, _log_audit
from app.utils.event_meals import (
    MealSelectionError,
    meals_enabled,
    option_price,
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


def purchased_meal_options(db: Session, registration_id: UUID) -> dict[str, EventRegistrationOption]:
    """This registration's meal EventRegistrationOption rows, by option id — the purchase-time price/currency
    snapshot (Phase 2.8). Empty for a registration with no priced meal selections (free options, or a
    registration created before pricing existed) — that is the normal, common case, not an error."""
    rows = (
        db.query(EventRegistrationOption)
        .filter(EventRegistrationOption.registration_id == registration_id, EventRegistrationOption.option_type == "meal")
        .all()
    )
    return {row.option_id: row for row in rows}


def attendee_meal_selections(
    options: list[dict], selected, purchased: dict[str, EventRegistrationOption] | None = None
) -> list[AttendeeMealSelection]:
    """``purchased`` (optional — see purchased_meal_options) attaches the purchase-time price/currency snapshot
    and service window to each selection, for the authenticated participant's own ticket/registration response
    (Section 15). Omitted by callers that only need the plain name/active view (dashboard, attendee list) —
    identical output to before Phase 2.8 in that case."""
    purchased = purchased or {}
    views = []
    for view in selection_views(options, selected):
        option = next((o for o in options if o["id"] == view["meal_id"]), None)
        line = purchased.get(view["meal_id"])
        views.append(AttendeeMealSelection(
            **view,
            price=float(line.unit_price) if line else (float(option_price(option)) if option else None),
            currency=(line.currency if line else (option.get("currency") if option else None)),
            service_start_at=option.get("service_start_at") if option else None,
            service_end_at=option.get("service_end_at") if option else None,
            status="confirmed" if line else "selected",
        ))
    return views


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

    # Phase 2.8: this endpoint has never had a payment mechanism, and inventing one here (or a matching partial
    # refund on removal) is exactly the kind of workaround the spec forbids. So: free selections may still be
    # freely changed (unchanged behavior), but the moment a PAID option enters or leaves the set, reject —
    # paid selections are only ever created by checkout/registration/walk-in, which already handle money.
    options_by_id = {o["id"]: o for o in stored_options(event)}
    changed = (set(selections or []) ^ set(before)) - set()
    paid_changed = sorted(
        options_by_id[option_id]["name"] for option_id in changed
        if option_id in options_by_id and option_price(options_by_id[option_id]) > 0
    )
    if paid_changed:
        raise HTTPException(
            status_code=422,
            detail="Cannot change a paid meal selection here: " + ", ".join(paid_changed)
            + ". Paid selections are set at registration/checkout and cannot be self-service edited.",
        )

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
    purchased = purchased_meal_options(db, reg.id)
    return EventMealSelectionResponse(
        event_id=event_id, registration_id=reg.id,
        meal_selections=attendee_meal_selections(stored_options(event), reg.meal_selections, purchased),
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
    options), and no query at all in those cases. Retired options appear only while someone still holds them.

    Phase 2.8: ``selected_count``/``reserved_count`` reuse the existing JSONB-array count (identical figure —
    EventRegistrationOption rows are created in lock-step with meal_selections, see event_service.py) rather than
    a second query against the new table, so this stays exactly as cheap as before pricing existed.
    """
    options = stored_options(event)
    if not options or not meals_enabled(event):
        return []
    counts = selected_meal_counts(db, event.id)
    result = []
    for option in options:
        if not (option["active"] or counts.get(option["id"], 0) > 0):
            continue
        taken = counts.get(option["id"], 0)
        capacity = option.get("capacity")
        remaining = None if capacity is None else max(0, int(capacity) - taken)
        result.append(DashboardMeal(
            meal_id=option["id"], name=option["name"], selected_count=taken, active=option["active"],
            price=float(option_price(option)), currency=option.get("currency") or (event.currency or "INR"),
            capacity=capacity, reserved_count=taken, remaining_capacity=remaining,
            sold_out=remaining is not None and remaining <= 0,
        ))
    return result
