"""Display names for the people in a conversation.

The chat tables only hold user ids, so a customer's name has to come from the identity service. Names are
looked up in as few calls as possible (the tenant member list first, then one lookup per remaining user),
cached for a few minutes, and never fall back to an email address: an unresolved name is None and the client
shows its own placeholder.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable
from uuid import UUID

from app.services import invigorate_auth_client as identity

logger = logging.getLogger(__name__)

_NAME_TTL_SECONDS = 600.0
_MISS_TTL_SECONDS = 60.0
_TENANT_TTL_SECONDS = 300.0
_CACHE_MAX = 5000
_LOOKUP_WORKERS = 8

_names: dict[str, tuple[float, str | None]] = {}
_tenant_names: dict[str, tuple[float, dict[str, str]]] = {}


def _nested(record: dict | None) -> list[dict]:
    if not isinstance(record, dict):
        return []
    found = [record]
    for key in ("data", "user", "profile"):
        value = record.get(key)
        if isinstance(value, dict):
            found.append(value)
    return found


def _clean(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    # never hand an email address to the client as if it were a name
    return text if text and "@" not in text else None


def extract_display_name(record: dict | None) -> str | None:
    """The person's name from an identity record (flat or nested under data/user/profile)."""
    for candidate in _nested(record):
        for field in ("name", "fullName", "full_name", "displayName", "display_name"):
            name = _clean(candidate.get(field))
            if name:
                return name
        first = _clean(candidate.get("firstName") or candidate.get("first_name") or candidate.get("given_name"))
        last = _clean(candidate.get("lastName") or candidate.get("last_name") or candidate.get("family_name"))
        joined = " ".join(part for part in (first, last) if part)
        if joined:
            return joined
    return None


def _record_user_id(record: dict) -> str | None:
    for candidate in _nested(record):
        for field in ("user_id", "id", "keycloak_id"):
            raw = candidate.get(field)
            if raw:
                try:
                    return str(UUID(str(raw)))
                except (TypeError, ValueError):
                    continue
    return None


def _trim(cache: dict) -> None:
    if len(cache) >= _CACHE_MAX:
        cache.pop(min(cache, key=lambda k: cache[k][0]), None)


def _members_by_id(tenant_id: UUID) -> dict[str, str]:
    key = str(tenant_id)
    now = time.monotonic()
    hit = _tenant_names.get(key)
    if hit and hit[0] > now:
        return hit[1]
    names: dict[str, str] = {}
    try:
        for member in identity.list_tenant_users(tenant_id):
            user_id = _record_user_id(member)
            name = extract_display_name(member)
            if user_id and name:
                names[user_id] = name
    except Exception:
        logger.exception("Could not list tenant members for %s", tenant_id)
    if names:  # an empty answer is retried on the next request instead of being cached
        _trim(_tenant_names)
        _tenant_names[key] = (now + _TENANT_TTL_SECONDS, names)
    return names


def _lookup_one(user_id: str) -> str | None:
    try:
        return extract_display_name(identity.fetch_internal_user_by_id(user_id))
    except Exception:
        logger.exception("Could not look up user %s", user_id)
        return None


def resolve_display_names(
    user_ids: Iterable[UUID | str],
    *,
    tenant_ids: Iterable[UUID | None] = (),
    current_user: dict | None = None,
) -> dict[str, str | None]:
    """{user_id (str): name or None}. The caller's own name comes from their token, without a network call."""
    wanted = {str(u) for u in user_ids if u}
    result: dict[str, str | None] = {}
    now = time.monotonic()

    me = str((current_user or {}).get("id") or "")
    if me in wanted:
        own = _clean((current_user or {}).get("name"))
        if own:
            result[me] = own

    for uid in wanted - set(result):
        hit = _names.get(uid)
        if hit and hit[0] > now:
            result[uid] = hit[1]

    missing = wanted - set(result)
    for tenant_id in {t for t in tenant_ids if t}:
        if not missing:
            break
        members = _members_by_id(tenant_id)
        for uid in list(missing):
            if uid in members:
                result[uid] = members[uid]
                _trim(_names)
                _names[uid] = (now + _NAME_TTL_SECONDS, members[uid])
                missing.discard(uid)

    if missing:
        ordered = sorted(missing)
        with ThreadPoolExecutor(max_workers=min(_LOOKUP_WORKERS, len(ordered))) as pool:
            for uid, name in zip(ordered, pool.map(_lookup_one, ordered)):
                result[uid] = name
                _trim(_names)
                _names[uid] = (now + (_NAME_TTL_SECONDS if name else _MISS_TTL_SECONDS), name)

    return {uid: result.get(uid) for uid in wanted}


def clear_cache() -> None:
    _names.clear()
    _tenant_names.clear()
