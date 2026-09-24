"""Event type + module configuration (Phase 2.2).

``Event.event_type`` says what *kind* of event it is; ``Event.modules`` says which capabilities the
event actually has enabled. The type only supplies the DEFAULT modules for a new event — afterwards
``modules`` is the source of truth and is never recalculated from the type.

Everything here is pure: no database access and no writes (in particular nothing is "backfilled" from
a read path). The single place the defaults live is ``EVENT_TYPE_DEFAULT_MODULES``.

Legacy events (``event_type IS NULL`` / ``modules IS NULL``) are not migrated. They are *resolved*
on read from what the event actually does today, so introducing this layer can never switch off a
capability an existing event is already using (see ``legacy_modules``).

Phase 2.2 stores and exposes the configuration only; no endpoint enforces it yet.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, get_args

from app.schemas.common_schema import EventType

EVENT_TYPES: tuple[str, ...] = get_args(EventType)
DEFAULT_EVENT_TYPE = "other"

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


def _modules(**flags: bool) -> dict[str, bool]:
    assert set(flags) == set(EVENT_MODULE_KEYS), "every module must be listed for every event type"
    return {key: flags[key] for key in EVENT_MODULE_KEYS}


# Centralized default configuration by event type. Applied ONCE, when an event is created (or first
# configured); it is copied into Event.modules and never re-applied on read.
EVENT_TYPE_DEFAULT_MODULES: dict[str, dict[str, bool]] = {
    "conference": _modules(
        registration=True, tickets=True, sessions=True, check_in=True,
        online_meeting=False, custom_questions=False, meals=True, accommodation=True,
    ),
    "workshop": _modules(
        registration=True, tickets=True, sessions=True, check_in=True,
        online_meeting=False, custom_questions=False, meals=False, accommodation=False,
    ),
    "marathon": _modules(
        registration=True, tickets=True, sessions=False, check_in=True,
        online_meeting=False, custom_questions=False, meals=False, accommodation=False,
    ),
    "camp": _modules(
        registration=True, tickets=False, sessions=False, check_in=True,
        online_meeting=False, custom_questions=False, meals=True, accommodation=True,
    ),
    "private_function": _modules(
        registration=True, tickets=False, sessions=False, check_in=True,
        online_meeting=False, custom_questions=True, meals=True, accommodation=False,
    ),
    "webinar": _modules(
        registration=True, tickets=False, sessions=True, check_in=False,
        online_meeting=True, custom_questions=False, meals=False, accommodation=False,
    ),
    "other": _modules(
        registration=True, tickets=False, sessions=False, check_in=False,
        online_meeting=False, custom_questions=False, meals=False, accommodation=False,
    ),
}
assert set(EVENT_TYPE_DEFAULT_MODULES) == set(EVENT_TYPES), "EventType and the defaults table must stay in sync"


class EventModuleConfigError(ValueError):
    """A module override contradicts the event it is applied to (mapped to HTTP 422 by the service)."""


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


def default_modules_for(event_type: str | None) -> dict[str, bool]:
    """A fresh copy of the default modules for ``event_type`` (unknown/None -> the safest: "other")."""
    return dict(EVENT_TYPE_DEFAULT_MODULES.get(event_type or DEFAULT_EVENT_TYPE, EVENT_TYPE_DEFAULT_MODULES[DEFAULT_EVENT_TYPE]))


# ---------------------------------------------------------------------------------------------
# legacy fallback (read side)
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
    """The stored event type, or ``"other"`` for legacy rows (and for any unexpected stored value)."""
    stored = getattr(event, "event_type", None)
    return stored if isinstance(stored, str) and stored in EVENT_TYPES else DEFAULT_EVENT_TYPE


def resolve_event_modules(event: Any) -> dict[str, bool]:
    """The event's effective modules: persisted values win; legacy rows fall back to behaviour.

    A persisted dict that is missing a key (or holds a non-boolean for it) is completed per key from
    the legacy resolution rather than guessed, so a partially-populated value can never fail a read.
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
    return resolved


# ---------------------------------------------------------------------------------------------
# write side: create / update planning + validation
# ---------------------------------------------------------------------------------------------


def clean_overrides(overrides: Mapping[str, Any] | None) -> dict[str, bool]:
    """Only the keys the client actually set (schema validation already guarantees keys and bool values)."""
    return {key: value for key, value in (overrides or {}).items() if key in EVENT_MODULE_KEYS and isinstance(value, bool)}


def validate_module_overrides(overrides: Mapping[str, bool], *, delivery_mode: Any, is_paid: bool) -> None:
    """Reject only obvious contradictions in *explicitly requested* values. Storage-first: nothing else is enforced.

    - online_meeting=true needs an online or hybrid delivery_mode (the existing format field).
    - tickets=false is refused for a paid event: checkout sells through ticket types.

    Deliberately NOT validated: sessions=false with sessions present (disabling never deletes data),
    or values that came from an event-type default (a default is a starting point, not a request).
    """
    if overrides.get("online_meeting") is True and str(delivery_mode or "").lower() not in ONLINE_DELIVERY_MODES:
        raise EventModuleConfigError(
            "modules.online_meeting can only be enabled when delivery_mode is 'online' or 'hybrid'."
        )
    if overrides.get("tickets") is False and is_paid:
        raise EventModuleConfigError(
            "modules.tickets cannot be disabled for a paid event (pricing_type is 'paid' or a price / ticket types are set)."
        )


def _defaults_for_new_configuration(event_type: str, *, is_paid: bool) -> dict[str, bool]:
    modules = default_modules_for(event_type)
    if is_paid:
        # A type default must never switch ticketing off for an event that already charges.
        modules["tickets"] = True
    return modules


def modules_for_new_event(event_type: str | None, overrides: Mapping[str, Any] | None, event_like: Any) -> dict[str, bool] | None:
    """Modules to persist for a NEW event.

    - no type, no overrides -> None: the event stays "legacy" (fully backward compatible for clients
      that do not know about this feature).
    - type only            -> that type's defaults (tickets kept on if the event is paid).
    - type + overrides     -> the defaults with the explicit values applied on top.
    - overrides only       -> the event's behaviour-based (legacy) modules with the overrides on top.

    The full 8-key dict is always stored, so later edits to the defaults table never change an
    existing event.
    """
    explicit = clean_overrides(overrides)
    if event_type is None and not explicit:
        return None
    paid = is_paid_event(
        getattr(event_like, "pricing_type", None), getattr(event_like, "price", None), getattr(event_like, "ticket_types", None)
    )
    base = _defaults_for_new_configuration(event_type, is_paid=paid) if event_type else legacy_modules(event_like)
    return {**base, **explicit}


def plan_config_update(
    event: Any,
    new_event_type: str | None,
    overrides: Mapping[str, Any] | None,
    *,
    delivery_mode: Any,
    is_paid: bool,
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
    """
    explicit = clean_overrides(overrides)
    changes: dict[str, Any] = {}
    stored_modules = getattr(event, "modules", None)
    configured = isinstance(stored_modules, Mapping) and bool(stored_modules)

    if new_event_type is not None and new_event_type != getattr(event, "event_type", None):
        changes["event_type"] = new_event_type

    if explicit:
        validate_module_overrides(explicit, delivery_mode=delivery_mode, is_paid=is_paid)
        if configured:
            base = resolve_event_modules(event)
        elif new_event_type is not None:
            base = _defaults_for_new_configuration(new_event_type, is_paid=is_paid)
        else:
            base = legacy_modules(event)
        changes["modules"] = {**base, **explicit}
    elif new_event_type is not None and not configured and new_event_type != resolve_event_type(event):
        changes["modules"] = _defaults_for_new_configuration(new_event_type, is_paid=is_paid)

    return changes
