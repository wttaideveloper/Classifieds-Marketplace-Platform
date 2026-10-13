"""Display names for the people in a conversation.

The chat tables only hold user ids, so a customer's name has to come from the identity service. Names are
looked up in as few calls as possible (the tenant member list first, then one lookup per remaining user),
cached for a few minutes, and never fall back to an email address: an unresolved name is None and the client
shows its own placeholder.
"""

from __future__ import annotations

import hashlib
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
# user id -> the name last saved for them, so a name is written once per process and again only when it changes
_remembered: dict[str, str] = {}


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
    """Application user id of an identity record: ``userId`` on member records (their ``id`` /
    ``membershipId`` is the membership id), ``id`` on flat user records. Never the Keycloak id."""
    for candidate in _nested(record):
        user_id = identity.member_application_user_id(candidate)
        if user_id is not None:
            return str(user_id)
    return None


def _trim(cache: dict) -> None:
    if len(cache) >= _CACHE_MAX:
        cache.pop(min(cache, key=lambda k: cache[k][0]), None)


def _names_from(records) -> dict[str, str]:
    names: dict[str, str] = {}
    for member in records or []:
        user_id = _record_user_id(member)
        name = extract_display_name(member)
        if user_id and name:
            names[user_id] = name
    return names


def _cached_names(key: str, load) -> dict[str, str]:
    now = time.monotonic()
    hit = _tenant_names.get(key)
    if hit and hit[0] > now:
        return hit[1]
    try:
        names = load()
    except Exception:
        logger.exception("Could not list members for %s", key.split(":", 1)[0])
        names = {}
    if names:  # an empty answer is retried on the next request instead of being cached
        _trim(_tenant_names)
        _tenant_names[key] = (now + _TENANT_TTL_SECONDS, names)
    return names


def _members_by_id(tenant_id: UUID, access_token: str | None = None) -> dict[str, str]:
    """Names of one tenant's users via Identity ``GET /internal/tenants/{id}/users``
    (``X-Internal-Api-Key`` + the caller's Bearer token)."""
    return _cached_names(
        f"tenant:{tenant_id}:{bool(access_token)}",
        lambda: _names_from(identity.list_tenant_users(tenant_id, access_token)),
    )


def _caller_tenant_names(access_token: str) -> dict[str, str]:
    """Names of the caller's own tenant via ``GET /tenant/members`` (Bearer token only). Used when the
    conversation carries no tenant or the internal listing yielded nothing; callers only read the ids
    they asked for, so another tenant's members are never surfaced."""
    key = "me:" + hashlib.sha256(access_token.encode()).hexdigest()
    return _cached_names(key, lambda: _names_from(identity.list_tenant_members(access_token)))


def remember_display_name(db, current_user: dict | None) -> None:
    """Save the signed-in person's own display name (from their token) so the other side of their conversations can
    show it. Called when someone uses chat. Writes only when the name is new or changed; never stores an e-mail
    address; never raises."""
    uid = str((current_user or {}).get("id") or "")
    name = _clean((current_user or {}).get("name"))
    if not uid or not name or _remembered.get(uid) == name:
        return
    try:
        from app.models.chat_model import UserDisplayName

        key = UUID(uid)
        with db.begin_nested():
            row = db.get(UserDisplayName, key)
            if row is None:
                db.add(UserDisplayName(user_id=key, display_name=name[:255], source="token"))
            elif row.display_name != name:
                row.display_name = name[:255]
        db.commit()
        _remembered[uid] = name
    except Exception:
        logger.debug("could not save the display name of %s", uid, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass


def _learned_names(db, ids: set[str]) -> dict[str, str]:
    """Names people gave through their own sign-in (see remember_display_name)."""
    if not ids:
        return {}
    try:
        from app.models.chat_model import UserDisplayName

        keys = []
        for i in ids:
            try:
                keys.append(UUID(i))
            except ValueError:
                continue
        with db.begin_nested():
            rows = db.query(UserDisplayName.user_id, UserDisplayName.display_name).filter(UserDisplayName.user_id.in_(keys)).all()
        return {str(uid): name for uid, name in rows if _clean(name)}
    except Exception:
        logger.debug("could not read the saved display names", exc_info=True)
        return {}


def _record_names(db, ids: set[str]) -> dict[str, str]:
    """Names this system already holds for these user ids, from what the person themself typed: a training
    enrolment, a delivery address, an order, a review. The first source that has a real name wins."""
    if not ids:
        return {}
    keys = []
    for i in ids:
        try:
            keys.append(UUID(i))
        except ValueError:
            continue
    sources = []
    try:
        from app.models.training_model import TrainingEnrolment
        sources.append((TrainingEnrolment.user_id, TrainingEnrolment.participant_name, TrainingEnrolment.created_at))
    except Exception:
        pass
    try:
        from app.models.address_model import UserAddress
        sources.append((UserAddress.user_id, UserAddress.full_name, None))
    except Exception:
        pass
    try:
        from app.models.cart_model import Order
        sources.append((Order.user_id, Order.shipping_full_name, Order.created_at))
    except Exception:
        pass
    try:
        from app.models.product_model import ProductReview
        sources.append((ProductReview.user_id, ProductReview.reviewer_name, ProductReview.created_at))
        from app.models.service_model import ServiceReview
        sources.append((ServiceReview.user_id, ServiceReview.reviewer_name, ServiceReview.created_at))
    except Exception:
        pass

    found: dict[str, str] = {}
    for user_col, name_col, order_col in sources:
        if len(found) == len(keys):
            break
        try:
            query = db.query(user_col, name_col).filter(user_col.in_(keys), name_col.isnot(None))
            if order_col is not None:
                query = query.order_by(order_col.desc())  # the most recent name they gave
            with db.begin_nested():
                rows = query.all()
        except Exception:
            logger.debug("could not read names from %s", getattr(user_col, "table", user_col), exc_info=True)
            continue
        for uid, raw in rows:
            clean = _clean(raw)
            if clean:
                found.setdefault(str(uid), clean)
    return found


def _lookup_one(user_id: str) -> str | None:
    try:
        record = identity.fetch_internal_user_by_id(user_id)
        # The lookup endpoint is not part of the documented Identity API and may answer with an
        # unfiltered list: never use a record that identifies a different user.
        returned_id = _record_user_id(record)
        if returned_id is not None and returned_id != user_id:
            return None
        return extract_display_name(record)
    except Exception:
        logger.exception("Could not look up user %s", user_id)
        return None


def resolve_display_names(
    user_ids: Iterable[UUID | str],
    *,
    tenant_ids: Iterable[UUID | None] = (),
    current_user: dict | None = None,
    access_token: str | None = None,
    db=None,
) -> dict[str, str | None]:
    """{user_id (str): name or None}. The caller's own name comes from their token, without a network call.

    Order: the caller's own token, the cache, the identity service's tenant member lists, then (with `db`) the name
    the person's own sign-in last gave us and names they typed into this system's own records, and last one
    identity lookup per person still unresolved. Never an e-mail address."""
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
        members = _members_by_id(tenant_id, access_token)
        for uid in list(missing):
            if uid in members:
                result[uid] = members[uid]
                _trim(_names)
                _names[uid] = (now + _NAME_TTL_SECONDS, members[uid])
                missing.discard(uid)

    if missing and access_token:
        members = _caller_tenant_names(access_token)
        for uid in list(missing):
            if uid in members:
                result[uid] = members[uid]
                _trim(_names)
                _names[uid] = (now + _NAME_TTL_SECONDS, members[uid])
                missing.discard(uid)

    if missing and db is not None:
        for source in (_learned_names, _record_names):
            for uid, name in source(db, set(missing)).items():
                result[uid] = name
                _trim(_names)
                _names[uid] = (now + _NAME_TTL_SECONDS, name)
                missing.discard(uid)
            if not missing:
                break

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
    _remembered.clear()
