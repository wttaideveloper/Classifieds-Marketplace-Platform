"""Event type + module configuration (Phase 2.2, evolved to a dynamic backend configuration system).

``Event.event_type`` says what *kind* of event it is; ``Event.modules`` says which capabilities the
event actually has enabled. The type only supplies the DEFAULT modules for a new event — afterwards
``modules`` is the source of truth and is never recalculated from the type.

Event types themselves are no longer a hardcoded Python table: they are rows of ``EventTypeConfig``
(``app/models/event_type_model.py``), managed through ``/api/v1/event-types`` (Super Admin writes,
public reads). This module stays pure — no database access, no writes — by taking an already-resolved
event type record (a plain dict: ``{key, default_modules, allowed_modules, required_modules}``, or
``None`` for "no type") wherever a caller previously passed a type name. Resolving that record (the DB
lookup, the "does it exist and is it active" check) is the service layer's job
(``app/services/event_service.py``); everything here is unchanged in spirit from Phase 2.2 otherwise.

Legacy events (``event_type IS NULL`` / ``modules IS NULL``) are not migrated. They are *resolved* on
read from what the event actually does today, so introducing this layer can never switch off a
capability an existing event is already using (see ``legacy_modules``).
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

# The supported modules — nothing else may be stored (see EventModulesInput, which forbids extras).
EVENT_MODULE_KEYS: tuple[str, ...] = (
    "registration",
    "tickets",
    "sessions",
    "check_in",
    "online_meeting",
    "custom_questions",
    "meals",
    "accommodation",
)

# Delivery modes that can host an online meeting.
ONLINE_DELIVERY_MODES = ("online", "hybrid")

# A stable, admin-curated Event Type key: lowercase snake_case, starts with a letter, max 30 characters
# (matches the existing events.event_type column width — no migration needed on that column).
EVENT_TYPE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,29}$")
EVENT_TYPE_KEY_MAX_LENGTH = 30

# Read-side fallback for a legacy event, or one whose stored event_type is missing/malformed. Also the
# seeded catch-all Event Type key (see the Phase 2.2 migration's seed data).
DEFAULT_EVENT_TYPE = "other"


class EventModuleConfigError(ValueError):
    """A module override, or an Event Type change, contradicts the event or its Event Type
    (mapped to HTTP 422 by the service)."""


# ---------------------------------------------------------------------------------------------
# small pure helpers
# ---------------------------------------------------------------------------------------------


def _to_float(value: Any) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def _ticket_price(ticket: Any) -> float:
    price = ticket.get("price") if isinstance(ticket, Mapping) else getattr(ticket, "price", None)
    return _to_float(price)


def is_paid_event(pricing_type: Any, price: Any, ticket_types: Any) -> bool:
    """True when the event charges: ``pricing_type == "paid"``, a positive top-level price, or any priced ticket type.

    Mirrors how the registration/checkout flow decides an event is paid (Phase 2.1), so a config
    default can never contradict pricing that checkout already relies on.
    """
    if str(pricing_type or "").lower() == "paid":
        return True
    if _to_float(price) > 0:
        return True
    return any(_ticket_price(ticket) > 0 for ticket in (ticket_types or []))


# ---------------------------------------------------------------------------------------------
# legacy fallback (read side) — unchanged from Phase 2.2
# ---------------------------------------------------------------------------------------------


def legacy_modules(event: Any) -> dict[str, bool]:
    """Modules for an event that has none stored, derived from what the event does today.

    Each capability the platform already offers stays ON when the event uses it, so nothing an
    existing event relies on is disabled by this layer:

    - registration   : always (every event can be registered for)
    - check_in       : always (check-in works for any registered event)
    - tickets        : the event is paid or has ticket types
    - sessions       : the event has agenda sessions
    - online_meeting : delivery_mode is online/hybrid, or a meeting link/provider is set
    - custom_questions: registration questions / custom values exist, or the event is pinned to an
                        Event Form version (whose custom fields the registration form serves)
    - meals, accommodation: not existing functionality -> off

    The result is computed, never stored, so it follows the event as it changes.
    """
    delivery = str(getattr(event, "delivery_mode", None) or "").lower()
    return {
        "registration": True,
        "tickets": is_paid_event(
            getattr(event, "pricing_type", None), getattr(event, "price", None), getattr(event, "ticket_types", None)
        ),
        "sessions": bool(getattr(event, "sessions", None)),
        "check_in": True,
        "online_meeting": (
            delivery in ONLINE_DELIVERY_MODES
            or bool(getattr(event, "meeting_link", None))
            or bool(getattr(event, "meeting_provider", None))
        ),
        "custom_questions": (
            bool(getattr(event, "custom_fields", None))
            or bool(getattr(event, "custom_values", None))
            or bool(getattr(event, "form_configuration_version_id", None))
        ),
        "meals": False,
        "accommodation": False,
    }


def resolve_event_type(event: Any) -> str:
    """The stored event type, or ``"other"`` for a legacy row or anything unexpected.

    Pure and DB-free: whatever is stored was validated against the Event Type table at write time, so
    a non-empty stored string is returned as-is (never re-checked against the current, possibly since-
    changed, Event Type table — deactivating or deleting a type must never invalidate an event that
    already used it). Anything that is not a non-empty string (NULL, wrong-shaped legacy data) falls
    back to ``"other"``.
    """
    stored = getattr(event, "event_type", None)
    return stored if isinstance(stored, str) and stored.strip() else DEFAULT_EVENT_TYPE


def resolve_event_modules(event: Any) -> dict[str, bool]:
    """The event's effective modules: persisted values win; legacy rows fall back to behaviour.

    A persisted dict that is missing a key (or holds a non-boolean for it) is completed per key from
    the legacy resolution rather than guessed, so a partially-populated value can never fail a read.

    ``registration`` is always forced True regardless of what is stored: it is mandatory for every
    event (product rule), and every write path already refuses to persist it as False — this is the
    read-side backstop so a stray/legacy row can never resolve as registration-disabled either.
    """
    stored = getattr(event, "modules", None)
    if not isinstance(stored, Mapping) or not stored:
        return legacy_modules(event)
    fallback: dict[str, bool] | None = None
    resolved: dict[str, bool] = {}
    for key in EVENT_MODULE_KEYS:
        value = stored.get(key)
        if isinstance(value, bool):
            resolved[key] = value
        else:
            fallback = fallback or legacy_modules(event)
            resolved[key] = fallback[key]
    resolved["registration"] = True
    return resolved


# ---------------------------------------------------------------------------------------------
# write side: create / update planning + validation against an Event Type record
# ---------------------------------------------------------------------------------------------


def clean_overrides(overrides: Mapping[str, Any] | None) -> dict[str, bool]:
    """Only the keys the client actually set (schema validation already guarantees keys and bool values)."""
    return {key: value for key, value in (overrides or {}).items() if key in EVENT_MODULE_KEYS and isinstance(value, bool)}


def validate_module_overrides(overrides: Mapping[str, bool], *, delivery_mode: Any, is_paid: bool) -> None:
    """Reject only obvious contradictions in *explicitly requested* values, independent of any Event Type.

    - registration=false is refused outright: registration is mandatory for every event (product rule),
      unconditionally — checked here (not only via an Event Type's required_modules) so a request with
      no event_type at all is covered too.
    - online_meeting=true needs an online or hybrid delivery_mode (the existing format field).
    - tickets=false is refused for a paid event: checkout sells through ticket types.

    Deliberately NOT validated: sessions=false with sessions present (disabling never deletes data),
    or values that came from an Event Type default (a default is a starting point, not a request).
    Event-Type-specific allowed/required rules are checked separately by ``check_type_constraints``.
    """
    if overrides.get("registration") is False:
        raise EventModuleConfigError(
            "modules.registration cannot be disabled: every event requires: registration to remain enabled."
        )
    if overrides.get("online_meeting") is True and str(delivery_mode or "").lower() not in ONLINE_DELIVERY_MODES:
        raise EventModuleConfigError(
            "modules.online_meeting can only be enabled when delivery_mode is 'online' or 'hybrid'."
        )
    if overrides.get("tickets") is False and is_paid:
        raise EventModuleConfigError(
            "modules.tickets cannot be disabled for a paid event (pricing_type is 'paid' or a price / ticket types are set)."
        )


def check_type_constraints(
    modules: Mapping[str, bool], event_type_record: Mapping[str, Any], *, changed_keys: Any = None
) -> None:
    """Raise ``EventModuleConfigError`` when ``modules`` is incompatible with the Event Type's
    ``allowed_modules``/``required_modules``.

    ``changed_keys`` (an iterable of module names), when given, restricts the check to those keys —
    used when validating explicit overrides, so the error names only what the caller actually touched.
    ``None`` checks every module key — used when an Event Type change must revalidate the event's
    entire persisted configuration, touched or not.
    """
    allowed = event_type_record["allowed_modules"]
    required = event_type_record["required_modules"]
    keys = list(changed_keys) if changed_keys is not None else list(EVENT_MODULE_KEYS)
    not_allowed = sorted(key for key in keys if modules.get(key) and not allowed.get(key))
    missing_required = sorted(key for key in keys if required.get(key) and not modules.get(key))
    if not_allowed:
        raise EventModuleConfigError(
            f"Event type '{event_type_record['key']}' does not allow: {', '.join(not_allowed)}."
        )
    if missing_required:
        raise EventModuleConfigError(
            f"Event type '{event_type_record['key']}' requires: {', '.join(missing_required)}; "
            "it cannot be disabled for this event type."
        )


def _apply_paid_ticket_safeguard(
    base: dict[str, bool], event_type_record: Mapping[str, Any], *, is_paid: bool, explicit: Mapping[str, bool]
) -> dict[str, bool]:
    """A type default must never leave ticketing off for an event that already charges — UNLESS the
    type's own ``allowed_modules`` forbids tickets entirely, in which case that is a genuine
    configuration conflict the caller must resolve (422), not something to silently override."""
    if is_paid and not base.get("tickets") and "tickets" not in explicit:
        if not event_type_record["allowed_modules"].get("tickets"):
            raise EventModuleConfigError(
                f"Event type '{event_type_record['key']}' does not allow tickets, but this event is paid. "
                "Choose an event type that allows tickets, or make the event free."
            )
        base["tickets"] = True
    return base


def modules_for_new_event(
    overrides: Mapping[str, Any] | None,
    event_like: Any,
    event_type_record: Mapping[str, Any] | None,
    *,
    is_paid: bool,
) -> dict[str, bool] | None:
    """Modules to persist for a NEW event.

    - no type record, no overrides -> None: the event stays "legacy" (fully backward compatible for
      clients that do not know about this feature).
    - type record only             -> that type's defaults (tickets kept on if the event is paid and
      the type allows it; 422 if the event is paid and the type does not allow tickets).
    - type record + overrides      -> the defaults with the explicit values applied on top, after
      checking the overrides against the type's allowed/required modules.
    - overrides only (no type)     -> the event's behaviour-based (legacy) modules with the overrides
      on top.

    The full 8-key dict is always stored, so later edits to an Event Type's defaults never change an
    existing event.
    """
    explicit = clean_overrides(overrides)
    if event_type_record is None and not explicit:
        return None
    if event_type_record is None:
        return {**legacy_modules(event_like), **explicit}
    if explicit:
        check_type_constraints(explicit, event_type_record, changed_keys=explicit.keys())
    base = _apply_paid_ticket_safeguard(dict(event_type_record["default_modules"]), event_type_record, is_paid=is_paid, explicit=explicit)
    return {**base, **explicit}


def plan_config_update(
    event: Any,
    new_event_type: str | None,
    overrides: Mapping[str, Any] | None,
    *,
    delivery_mode: Any,
    is_paid: bool,
    new_event_type_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute the config columns an update changes. Returns only the keys to set (possibly empty).

    Rules (partial-update semantics; ``None`` / omitted means "no change"):

    1. Unrelated updates never touch either column.
    2. ``modules`` given  -> merged over the current effective modules; unspecified keys keep their value.
       Base = persisted modules; else (first time configured) the given type's defaults; else the
       event's behaviour-based modules.
    3. ``event_type`` given -> stored as given. It never rewrites *persisted* modules. Only an event
       that was never configured (modules NULL) gets the new type's defaults, and only when the type
       actually differs from its current one — so a client that echoes back the "other" it read from a
       legacy event does not silently reset that event's modules to registration-only.
    4. Changing the Event Type on an already-configured event never rewrites its persisted modules —
       but if those persisted modules are incompatible with the NEW type's allowed/required modules
       (with any overrides given in the same request applied first), the whole update is rejected
       (422) naming the incompatible modules, rather than silently applying an invalid combination.

    ``new_event_type_record`` is the resolved Event Type row (``{key, default_modules, allowed_modules,
    required_modules}``) for ``new_event_type`` — only needed (and only looked up by the caller) when
    ``new_event_type`` actually differs from the event's current stored type.
    """
    explicit = clean_overrides(overrides)
    changes: dict[str, Any] = {}
    stored_modules = getattr(event, "modules", None)
    configured = isinstance(stored_modules, Mapping) and bool(stored_modules)
    type_is_changing = new_event_type is not None and new_event_type != getattr(event, "event_type", None)

    if type_is_changing:
        changes["event_type"] = new_event_type

    if explicit:
        validate_module_overrides(explicit, delivery_mode=delivery_mode, is_paid=is_paid)
        if new_event_type_record is not None:
            check_type_constraints(explicit, new_event_type_record, changed_keys=explicit.keys())
        if configured:
            base = dict(resolve_event_modules(event))
        elif type_is_changing and new_event_type_record is not None:
            base = _apply_paid_ticket_safeguard(
                dict(new_event_type_record["default_modules"]), new_event_type_record, is_paid=is_paid, explicit=explicit
            )
        else:
            base = legacy_modules(event)
        merged = {**base, **explicit}
        if new_event_type_record is not None:
            check_type_constraints(merged, new_event_type_record, changed_keys=None)
        changes["modules"] = merged
    elif type_is_changing and not configured and new_event_type != resolve_event_type(event):
        if new_event_type_record is not None:
            changes["modules"] = _apply_paid_ticket_safeguard(
                dict(new_event_type_record["default_modules"]), new_event_type_record, is_paid=is_paid, explicit={}
            )
    elif type_is_changing and configured and new_event_type_record is not None:
        # No explicit override: the persisted modules are kept exactly as they are — but they must
        # still make sense under the new Event Type, or the change is rejected outright.
        check_type_constraints(resolve_event_modules(event), new_event_type_record, changed_keys=None)

    return changes
