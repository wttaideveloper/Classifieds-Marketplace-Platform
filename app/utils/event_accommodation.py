"""Accommodation configuration for an Event (Phase 2.7, priced in Phase 2.8): options, stable ids, and selection
validation.

Accommodation is deliberately small, and works exactly like meals (Phase 2.6/2.8, ``app/utils/event_meals.py``):
an event has a list of accommodation *options* (id + name, optionally a description) and an attendee's
registration records which option ids they need. Phase 2.8 adds real pricing and capacity — see the identical
reasoning in ``event_meals.py``'s module docstring; every new field here is optional and defaults to "free,
unlimited, always open" so pre-Phase-2.8 options keep working unchanged.

Single source of truth for "is accommodation on": ``modules.accommodation`` (Phase 2.2, via ``resolve_event_modules``).
This module never stores or reads a second enabled flag; the option list lives in ``Event.accommodation`` as
``{"options": [...]}`` and is meaningful only while the module is on. Turning the module off keeps the options.

Everything here is pure: no database access, no writes, and reads never raise on legacy or odd stored data (legacy
events have ``accommodation = NULL`` and resolve to "no options", accommodation off). Capacity ENFORCEMENT lives in
``app.services.event_option_pricing_service``, same as meals.

Option identity is the ``id``, never the list position. Registrations reference ids, so an update keeps an option's id
when it is retained, and an option that an update no longer lists is NOT deleted: it stays in the config with
``active: false`` so historical selections keep resolving, and it can no longer be newly selected.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.utils.event_modules import resolve_event_modules
from app.utils.event_payments import parse_money
from app.utils.event_utils import get_event_timezone, resolve_naive_or_aware

# Safe, URL/JSON-friendly ids: a uuid4 string, or a readable slug such as "shared-room".
ACCOMMODATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_INPUT_OPTIONS = 50  # options one request may carry
MAX_STORED_OPTIONS = 100  # active + retired options an event may accumulate
NAME_MAX_LENGTH = 100
DESCRIPTION_MAX_LENGTH = 300
ZERO = Decimal("0")


class AccommodationConfigError(ValueError):
    """The accommodation configuration is invalid or contradicts the event's modules (mapped to HTTP 422 by the service)."""


class AccommodationSelectionError(ValueError):
    """An attendee's accommodation selection is invalid for the event (mapped to HTTP 422 by the service)."""


def _normalize_price(value: Any) -> str:
    """See event_meals._normalize_price — identical reasoning, including why both branches quantize to 2dp
    (round-trip stability for an echoed edit form; a mismatched scale here falsely looks like a real change)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return "0.00"
    parsed = parse_money(value if isinstance(value, str) else str(value))
    if parsed is None or parsed < ZERO:
        raise AccommodationConfigError(f"Invalid accommodation option price: {value!r}")
    return str(parsed.quantize(Decimal("0.01")))


def _normalize_capacity(value: Any) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        capacity = int(value)
    except (TypeError, ValueError):
        raise AccommodationConfigError(f"Invalid accommodation option capacity: {value!r}")
    if capacity < 0:
        raise AccommodationConfigError("Accommodation option capacity cannot be negative.")
    return capacity


def _normalize_window_value(value: Any, field: str) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        try:
            datetime.fromisoformat(value.strip())
        except ValueError:
            raise AccommodationConfigError(f"Invalid {field}: {value!r} (expected an ISO 8601 datetime)")
        return value.strip()
    raise AccommodationConfigError(f"Invalid {field}: {value!r}")


def _normalize_windows(item: Mapping) -> dict[str, str | None]:
    windows = {
        "purchase_start_at": _normalize_window_value(item.get("purchase_start_at"), "purchase_start_at"),
        "purchase_end_at": _normalize_window_value(item.get("purchase_end_at"), "purchase_end_at"),
        "service_start_at": _normalize_window_value(item.get("service_start_at"), "service_start_at"),
        "service_end_at": _normalize_window_value(item.get("service_end_at"), "service_end_at"),
    }
    if windows["purchase_start_at"] and windows["purchase_end_at"]:
        if windows["purchase_start_at"] >= windows["purchase_end_at"]:
            raise AccommodationConfigError("purchase_start_at must be before purchase_end_at.")
    if windows["service_start_at"] and windows["service_end_at"]:
        if windows["service_start_at"] >= windows["service_end_at"]:
            raise AccommodationConfigError("service_start_at must be before service_end_at.")
    return windows


def option_price(option: Mapping) -> Decimal:
    """See event_meals.option_price — identical reasoning, including always quantizing to 2dp and never using
    the ``parse_money(...) or ZERO`` pattern (a legitimately-parsed zero Decimal is falsy)."""
    try:
        parsed = parse_money(str(option.get("price", "0")))
    except (InvalidOperation, TypeError):
        parsed = None
    return (parsed if parsed is not None else ZERO).quantize(Decimal("0.01"))


def is_within_purchase_window(option: Mapping, now: datetime, event_tz) -> bool:
    """See event_meals.is_within_purchase_window — identical reasoning: ``now`` must be aware UTC;
    ``event_tz`` (app.utils.event_utils.get_event_timezone(event)) resolves a naive purchase_start_at/
    purchase_end_at as the organizer's own event-local wall-clock time, never assumed UTC. An
    already timezone-aware stored value is trusted as-is."""
    start, end = option.get("purchase_start_at"), option.get("purchase_end_at")
    start_at = resolve_naive_or_aware(datetime.fromisoformat(start), event_tz) if start else None
    end_at = resolve_naive_or_aware(datetime.fromisoformat(end), event_tz) if end else None
    if start_at and now < start_at:
        return False
    if end_at and now >= end_at:
        return False
    return True


def has_service_period_ended(option: Mapping, now: datetime, event_tz) -> bool:
    """Whether a new accommodation selection is for a completed stay period."""
    end = option.get("service_end_at")
    if not end:
        return False
    end_at = resolve_naive_or_aware(datetime.fromisoformat(end), event_tz)
    return now >= end_at


def remaining_capacity(option: Mapping, taken: int) -> int | None:
    capacity = option.get("capacity")
    if capacity is None:
        return None
    return max(0, int(capacity) - taken)


def is_sold_out(option: Mapping, taken: int) -> bool:
    remaining = remaining_capacity(option, taken)
    return remaining is not None and remaining <= 0


# ---------------------------------------------------------------------------------------------
# read side
# ---------------------------------------------------------------------------------------------


def accommodation_enabled(event: Any) -> bool:
    """Whether accommodation is on: ``modules.accommodation`` (persisted, or resolved for a legacy event: off)."""
    return bool(resolve_event_modules(event).get("accommodation"))


def stored_accommodation_options(event: Any) -> list[dict]:
    """The event's accommodation options (active and retired), normalised. Tolerant: anything malformed is skipped.

    ``currency`` is always resolved to a concrete non-empty string here (the option's own, or the event's, or
    "INR") — never None — see event_meals.stored_options, identical reasoning.
    """
    raw = getattr(event, "accommodation", None)
    items = raw.get("options") if isinstance(raw, Mapping) else None
    event_currency = getattr(event, "currency", None) or "INR"
    options: list[dict] = []
    seen: set[str] = set()
    for item in items if isinstance(items, Sequence) and not isinstance(items, (str, bytes)) else []:
        if not isinstance(item, Mapping):
            continue
        option_id, name = item.get("id"), item.get("name")
        if not (isinstance(option_id, str) and ACCOMMODATION_ID_PATTERN.match(option_id) and isinstance(name, str) and name.strip()):
            continue
        if option_id.lower() in seen:
            continue
        seen.add(option_id.lower())
        description = item.get("description")
        try:
            price = _normalize_price(item.get("price"))
            capacity = _normalize_capacity(item.get("capacity"))
            windows = _normalize_windows(item)
        except AccommodationConfigError:
            price, capacity = "0", None
            windows = {"purchase_start_at": None, "purchase_end_at": None, "service_start_at": None, "service_end_at": None}
        options.append({
            "id": option_id,
            "name": name.strip(),
            "description": description if isinstance(description, str) and description.strip() else None,
            "active": item.get("active") is not False,
            "price": price,
            "currency": item.get("currency") if isinstance(item.get("currency"), str) and item.get("currency").strip() else event_currency,
            "capacity": capacity,
            **windows,
        })
    return options


def resolve_event_accommodation(event: Any) -> dict:
    """The ``accommodation`` object of an event response: ``enabled`` mirrors ``modules.accommodation``; options as stored."""
    return {"enabled": accommodation_enabled(event), "options": stored_accommodation_options(event)}


# ---------------------------------------------------------------------------------------------
# configuration (write side)
# ---------------------------------------------------------------------------------------------


def _text(value: Any) -> str | None:
    """A trimmed string, or None when absent/blank."""
    return str(value).strip() or None if value is not None else None


def _key(option: Mapping) -> str:
    """What makes two options "the same": the name, case-insensitively."""
    return str(option["name"]).strip().lower()


def build_accommodation_options(incoming: Sequence[Mapping], existing: Sequence[Mapping]) -> list[dict]:
    """The stored options after an update that lists ``incoming`` as the desired active set.

    - an option that carries an id keeps it (a known id is the same option: renamed, edited, reactivated);
    - one without an id is matched to an existing option with the same name and REUSES its id, so a client that does
      not track ids never orphans selections; otherwise it gets a new id;
    - options the update does not list are kept as ``active: false`` (history), never deleted;
    - ids are unique (case-insensitively) and no two active options share a name.
    """
    if len(incoming) > MAX_INPUT_OPTIONS:
        raise AccommodationConfigError(f"At most {MAX_INPUT_OPTIONS} accommodation options can be sent at once.")
    existing_by_key: dict[str, str] = {}
    for option in existing:
        existing_by_key.setdefault(_key(option), option["id"])
    taken_ids: set[str] = set()
    result: list[dict] = []
    used_existing_ids: set[str] = set()
    for item in incoming:
        name = (item.get("name") or "").strip()
        if not name:
            raise AccommodationConfigError("Every accommodation option needs a non-empty name.")
        option_id = item.get("id")
        if option_id is not None and not (isinstance(option_id, str) and ACCOMMODATION_ID_PATTERN.match(option_id)):
            raise AccommodationConfigError(
                "Accommodation option ids may only contain letters, digits, '_' and '-' (1 to 64 characters, starting with a letter or digit)."
            )
        option = {
            "id": option_id,
            "name": name,
            "description": _text(item.get("description")),
            "active": item.get("active") is not False,
            "price": _normalize_price(item.get("price")),
            "currency": _text(item.get("currency")),
            "capacity": _normalize_capacity(item.get("capacity")),
            **_normalize_windows(item),
        }
        if option["id"] is None:
            reused = existing_by_key.get(_key(option))
            option["id"] = reused if reused and reused.lower() not in taken_ids else str(uuid.uuid4())
        if option["id"].lower() in taken_ids:
            raise AccommodationConfigError(f"Duplicate accommodation option id: {option['id']}")
        taken_ids.add(option["id"].lower())
        used_existing_ids.add(option["id"])
        result.append(option)

    seen_keys: set[str] = set()
    for option in result:
        if option["active"]:
            key = _key(option)
            if key in seen_keys:
                raise AccommodationConfigError(f"Duplicate accommodation option: '{option['name']}'")
            seen_keys.add(key)

    for option in existing:  # retained as history, no longer offered
        if option["id"] not in used_existing_ids and option["id"].lower() not in taken_ids:
            result.append({**option, "active": False})
    if len(result) > MAX_STORED_OPTIONS:
        raise AccommodationConfigError(
            f"An event can hold at most {MAX_STORED_OPTIONS} accommodation options (including retired ones)."
        )
    return result


def accommodation_for_new_event(incoming: Sequence[Mapping] | None, *, enabled: bool) -> dict | None:
    """The ``accommodation`` value to store for a NEW event (None = nothing configured)."""
    if not incoming:
        return None
    if not enabled:
        raise AccommodationConfigError(
            "Accommodation is disabled for this event. Set modules.accommodation to true to configure accommodation options."
        )
    return {"options": build_accommodation_options(incoming, [])}


def plan_accommodation_update(event: Any, incoming: Sequence[Mapping] | None, *, enabled_after: bool) -> dict | None:
    """The new ``accommodation`` value for an update, or None when nothing changes.

    ``incoming`` None = "no change". Options can only be changed while the module is on (``enabled_after`` is the
    effective ``modules.accommodation`` once this same update is applied); a request that merely echoes the stored
    options back is a no-op even while it is off, so an edit form that round-trips the event keeps working.
    """
    if incoming is None:
        return None
    current = stored_accommodation_options(event)
    updated = build_accommodation_options(incoming, current)
    # See event_meals.plan_meals_update — identical reasoning: resolve currency before comparing, since
    # build_accommodation_options() deliberately leaves a freshly-submitted unset currency as None.
    event_currency = getattr(event, "currency", None) or "INR"
    comparable = [{**option, "currency": option["currency"] or event_currency} for option in updated]
    if comparable == current:
        return None
    if not enabled_after:
        raise AccommodationConfigError(
            "Accommodation is disabled for this event. Set modules.accommodation to true to change accommodation options."
        )
    return {"options": updated}


# ---------------------------------------------------------------------------------------------
# attendee selections
# ---------------------------------------------------------------------------------------------


def validate_accommodation_selections(
    event: Any, selections: Sequence[str] | None, current: Sequence[str] | None = None
) -> list[str] | None:
    """Check an attendee's selection against the event's accommodation configuration; return it normalised.

    None or empty = no selection (returns None, always allowed, so clients may always send the field). Otherwise
    accommodation must be on, every id must be one of the event's options (the configuration is the source of truth),
    and each option can appear once. Only ACTIVE options can be newly selected; an id the attendee already holds
    (``current``) may be kept even if the organizer has since retired it. The result is in the event's option order.
    """
    if not selections:
        return None
    if not accommodation_enabled(event):
        raise AccommodationSelectionError("Accommodation is not enabled for this event.")
    seen: set[str] = set()
    for selected in selections:
        if not isinstance(selected, str):
            raise AccommodationSelectionError("Accommodation selections must be a list of accommodation option ids.")
        if selected in seen:
            raise AccommodationSelectionError(f"Duplicate accommodation selection: {selected}")
        seen.add(selected)
    options = stored_accommodation_options(event)
    known = {option["id"]: option for option in options}
    unknown = sorted(selected for selected in seen if selected not in known)
    if unknown:
        raise AccommodationSelectionError("Unknown accommodation option(s) for this event: " + ", ".join(unknown))
    held = set(current or [])
    retired = sorted(selected for selected in seen if not known[selected]["active"] and selected not in held)
    if retired:
        raise AccommodationSelectionError(
            "Accommodation option(s) no longer available: " + ", ".join(known[i]["name"] for i in retired)
        )
    now = datetime.now(timezone.utc)
    event_tz = get_event_timezone(event)
    closed = sorted(
        selected for selected in seen
        if selected not in held and not is_within_purchase_window(known[selected], now, event_tz)
    )
    if closed:
        raise AccommodationSelectionError(
            "Accommodation option(s) outside their purchase window: " + ", ".join(known[i]["name"] for i in closed)
        )
    service_ended = sorted(
        selected for selected in seen
        if selected not in held and has_service_period_ended(known[selected], now, event_tz)
    )
    if service_ended:
        raise AccommodationSelectionError(
            "Accommodation period has ended: " + ", ".join(known[i]["name"] for i in service_ended)
        )
    return [option["id"] for option in options if option["id"] in seen]


def accommodation_selection_views(options: Sequence[Mapping], selected: Sequence[str] | None) -> list[dict]:
    """A registration's stored selections with their names, in the event's option order (``options`` is
    ``stored_accommodation_options(event)``, computed once per request). Unknown ids are shown, not hidden."""
    if not isinstance(selected, Sequence) or isinstance(selected, (str, bytes)):
        return []
    known = {option["id"]: option for option in options}
    wanted = [item for item in selected if isinstance(item, str)]
    ordered = [option["id"] for option in options if option["id"] in wanted]
    ordered += [item for item in dict.fromkeys(wanted) if item not in known]
    return [
        {
            "accommodation_id": item,
            "name": known[item]["name"] if item in known else None,
            "active": known[item]["active"] if item in known else False,
        }
        for item in ordered
    ]
