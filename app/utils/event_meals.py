"""Meal configuration for an Event (Phase 2.6): options, stable ids, and selection validation.

Meals are deliberately small: an event has a list of meal *options* (id + name, optionally a description and a
date) and an attendee's registration records which option ids they selected. No prices, capacities, vendors or
menus.

Single source of truth for "are meals on": ``modules.meals`` (Phase 2.2, via ``resolve_event_modules``). This
module never stores or reads a second enabled flag; the option list lives in ``Event.meals`` as
``{"options": [...]}`` and is meaningful only while the module is on. Turning the module off keeps the options.

Everything here is pure: no database access, no writes, and reads never raise on legacy or odd stored data
(legacy events have ``meals = NULL`` and resolve to "no options", meals off).

Option identity is the ``id``, never the list position. Registrations reference ids, so an update keeps an
option's id when it is retained, and an option that an update no longer lists is NOT deleted: it stays in the
config with ``active: false`` so historical selections keep resolving, and it can no longer be newly selected.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from app.utils.event_modules import resolve_event_modules

# Safe, URL/JSON-friendly ids: a uuid4 string, or a readable slug such as "breakfast-day-1".
MEAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_INPUT_OPTIONS = 50  # options one request may carry
MAX_STORED_OPTIONS = 100  # active + retired options an event may accumulate
NAME_MAX_LENGTH = 100
DESCRIPTION_MAX_LENGTH = 300


class MealConfigError(ValueError):
    """The meal configuration is invalid or contradicts the event's modules (mapped to HTTP 422 by the service)."""


class MealSelectionError(ValueError):
    """An attendee's meal selection is invalid for the event (mapped to HTTP 422 by the service)."""


# ---------------------------------------------------------------------------------------------
# read side
# ---------------------------------------------------------------------------------------------


def meals_enabled(event: Any) -> bool:
    """Whether meals are on: ``modules.meals`` (persisted, or resolved for a legacy event: off)."""
    return bool(resolve_event_modules(event).get("meals"))


def stored_options(event: Any) -> list[dict]:
    """The event's meal options (active and retired), normalised. Tolerant: anything malformed is skipped."""
    raw = getattr(event, "meals", None)
    items = raw.get("options") if isinstance(raw, Mapping) else None
    options: list[dict] = []
    seen: set[str] = set()
    for item in items if isinstance(items, Sequence) and not isinstance(items, (str, bytes)) else []:
        if not isinstance(item, Mapping):
            continue
        option_id, name = item.get("id"), item.get("name")
        if not (isinstance(option_id, str) and MEAL_ID_PATTERN.match(option_id) and isinstance(name, str) and name.strip()):
            continue
        if option_id.lower() in seen:
            continue
        seen.add(option_id.lower())
        description, date = item.get("description"), item.get("date")
        options.append({
            "id": option_id,
            "name": name.strip(),
            "description": description if isinstance(description, str) and description.strip() else None,
            "date": date if isinstance(date, str) and date.strip() else None,
            "active": item.get("active") is not False,
        })
    return options


def resolve_event_meals(event: Any) -> dict:
    """The ``meals`` object of an event response: ``enabled`` mirrors ``modules.meals``; options as stored."""
    return {"enabled": meals_enabled(event), "options": stored_options(event)}


# ---------------------------------------------------------------------------------------------
# configuration (write side)
# ---------------------------------------------------------------------------------------------


def _text(value: Any) -> str | None:
    """A trimmed string, or None when absent/blank."""
    return str(value).strip() or None if value is not None else None


def _key(option: Mapping) -> tuple[str, str | None]:
    return (str(option["name"]).strip().lower(), option.get("date"))


def build_options(incoming: Sequence[Mapping], existing: Sequence[Mapping]) -> list[dict]:
    """The stored options after an update that lists ``incoming`` as the desired active set.

    - an option that carries an id keeps it (a known id is the same option: renamed, redated, reactivated);
    - one without an id is matched to an existing option with the same name and date and REUSES its id, so a
      client that does not track ids never orphans selections; otherwise it gets a new id;
    - options the update does not list are kept as ``active: false`` (history), never deleted;
    - ids are unique (case-insensitively) and no two active options share a name and date.
    """
    if len(incoming) > MAX_INPUT_OPTIONS:
        raise MealConfigError(f"At most {MAX_INPUT_OPTIONS} meal options can be sent at once.")
    existing_by_key: dict[tuple, str] = {}
    for option in existing:
        existing_by_key.setdefault(_key(option), option["id"])
    taken_ids: set[str] = set()
    result: list[dict] = []
    used_existing_ids: set[str] = set()
    for item in incoming:
        name = (item.get("name") or "").strip()
        if not name:
            raise MealConfigError("Every meal option needs a non-empty name.")
        option_id = item.get("id")
        if option_id is not None and not (isinstance(option_id, str) and MEAL_ID_PATTERN.match(option_id)):
            raise MealConfigError(
                "Meal option ids may only contain letters, digits, '_' and '-' (1 to 64 characters, starting with a letter or digit)."
            )
        option = {
            "id": option_id,
            "name": name,
            "description": _text(item.get("description")),
            "date": _text(item.get("date")),
            "active": item.get("active") is not False,
        }
        if option["id"] is None:
            reused = existing_by_key.get(_key(option))
            option["id"] = reused if reused and reused.lower() not in taken_ids else str(uuid.uuid4())
        if option["id"].lower() in taken_ids:
            raise MealConfigError(f"Duplicate meal option id: {option['id']}")
        taken_ids.add(option["id"].lower())
        used_existing_ids.add(option["id"])
        result.append(option)

    seen_keys: set[tuple] = set()
    for option in result:
        if option["active"]:
            key = _key(option)
            if key in seen_keys:
                raise MealConfigError(f"Duplicate meal option: '{option['name']}'" + (f" on {option['date']}" if option["date"] else ""))
            seen_keys.add(key)

    for option in existing:  # retained as history, no longer offered
        if option["id"] not in used_existing_ids and option["id"].lower() not in taken_ids:
            result.append({**option, "active": False})
    if len(result) > MAX_STORED_OPTIONS:
        raise MealConfigError(f"An event can hold at most {MAX_STORED_OPTIONS} meal options (including retired ones).")
    return result


def meals_for_new_event(incoming: Sequence[Mapping] | None, *, enabled: bool) -> dict | None:
    """The ``meals`` value to store for a NEW event (None = nothing configured)."""
    if not incoming:
        return None
    if not enabled:
        raise MealConfigError("Meals are disabled for this event. Set modules.meals to true to configure meal options.")
    return {"options": build_options(incoming, [])}


def plan_meals_update(event: Any, incoming: Sequence[Mapping] | None, *, enabled_after: bool) -> dict | None:
    """The new ``meals`` value for an update, or None when nothing changes.

    ``incoming`` None = "no change". Meal options can only be changed while the module is on (``enabled_after`` is
    the effective ``modules.meals`` once this same update is applied); a request that merely echoes the stored
    options back is a no-op even while it is off, so an edit form that round-trips the event keeps working.
    """
    if incoming is None:
        return None
    current = stored_options(event)
    updated = build_options(incoming, current)
    if updated == current:
        return None
    if not enabled_after:
        raise MealConfigError("Meals are disabled for this event. Set modules.meals to true to change meal options.")
    return {"options": updated}


# ---------------------------------------------------------------------------------------------
# attendee selections
# ---------------------------------------------------------------------------------------------


def validate_meal_selections(event: Any, selections: Sequence[str] | None, current: Sequence[str] | None = None) -> list[str] | None:
    """Check an attendee's selection against the event's meal configuration; return it normalised.

    None or empty = no selection (returns None, always allowed, so clients may always send the field). Otherwise
    meals must be on, every id must be one of the event's options (the configuration is the source of truth), and
    each option can appear once. Only ACTIVE options can be newly selected; an id the attendee already holds
    (``current``) may be kept even if the organizer has since retired it. The result is in the event's option order.
    """
    if not selections:
        return None
    if not meals_enabled(event):
        raise MealSelectionError("Meals are not enabled for this event.")
    seen: set[str] = set()
    for selected in selections:
        if not isinstance(selected, str):
            raise MealSelectionError("Meal selections must be a list of meal option ids.")
        if selected in seen:
            raise MealSelectionError(f"Duplicate meal selection: {selected}")
        seen.add(selected)
    options = stored_options(event)
    known = {option["id"]: option for option in options}
    unknown = sorted(selected for selected in seen if selected not in known)
    if unknown:
        raise MealSelectionError("Unknown meal option(s) for this event: " + ", ".join(unknown))
    held = set(current or [])
    retired = sorted(selected for selected in seen if not known[selected]["active"] and selected not in held)
    if retired:
        raise MealSelectionError("Meal option(s) no longer available: " + ", ".join(known[i]["name"] for i in retired))
    return [option["id"] for option in options if option["id"] in seen]


def selection_views(options: Sequence[Mapping], selected: Sequence[str] | None) -> list[dict]:
    """A registration's stored selections with their names, in the event's option order (``options`` is
    ``stored_options(event)``, computed once per request). Unknown ids are shown, not hidden."""
    if not isinstance(selected, Sequence) or isinstance(selected, (str, bytes)):
        return []
    known = {option["id"]: option for option in options}
    wanted = [item for item in selected if isinstance(item, str)]
    ordered = [option["id"] for option in options if option["id"] in wanted]
    ordered += [item for item in dict.fromkeys(wanted) if item not in known]
    return [
        {"meal_id": item, "name": known[item]["name"] if item in known else None, "active": known[item]["active"] if item in known else False}
        for item in ordered
    ]
