"""Validation helpers for Event configuration currency.

Event.currency is authoritative for new or edited configuration.  These helpers
do not read or write orders or registration-option snapshots, which deliberately
remain immutable purchase-time records.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any


_CURRENCY_CODE = re.compile(r"^[A-Za-z]{3}$")


class EventCurrencyConfigError(ValueError):
    """A currency configuration error to map to HTTP 422."""


def normalize_event_currency(value: Any, *, required: bool = False) -> str | None:
    """Normalize a three-letter code without inventing a value for legacy data."""
    if value is None or not str(value).strip():
        if required:
            raise EventCurrencyConfigError("Event currency is required.")
        return None
    currency = str(value).strip().upper()
    if not _CURRENCY_CODE.fullmatch(currency):
        raise EventCurrencyConfigError("Event currency must be a three-letter currency code.")
    return currency


def normalize_child_currencies(
    options: Iterable[Any] | None, event_currency: Any, option_kind: str,
) -> list[Any]:
    """Return shallow-normalized options, rejecting explicit mismatches.

    A missing child currency intentionally stays missing in storage: existing
    readers already interpret it as inherited from Event.currency.  Explicit
    values are normalized to uppercase so matching codes behave consistently at
    runtime too.
    """
    target = normalize_event_currency(event_currency, required=True)
    normalized: list[Any] = []
    for option in options or []:
        if not isinstance(option, Mapping):
            normalized.append(option)
            continue
        copied = dict(option)
        supplied = normalize_event_currency(copied.get("currency"))
        if supplied is not None and supplied != target:
            raise EventCurrencyConfigError(f"{option_kind} currency must match the Event currency ({target}).")
        if supplied is not None:
            copied["currency"] = supplied
        normalized.append(copied)
    return normalized
