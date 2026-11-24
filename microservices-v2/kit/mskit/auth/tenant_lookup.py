"""Enterprise -> tenant lookup used by tenant resolution (auth_context).

The enterprises table is owned by the catalog service. Every other service asks catalog's internal
API; catalog registers a local implementation (its own database) with ``register_local_lookup``.

Failure handling: 2 s timeout, GET retried only on refused connections (safe, read-only), results
cached for 60 s. When catalog cannot be reached the lookup answers None - exactly what the monolith
answered for an unknown enterprise - and tenant resolution moves on to the next fallback (the
Invigorate /tenant/me call), so a catalog outage degrades only the rare claim-less admin session.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable
from uuid import UUID

import httpx

from mskit.correlation import REQUEST_ID_HEADER, current_request_id
from mskit.http_client import RETRYABLE_EXCEPTIONS

logger = logging.getLogger(__name__)

_local: Callable | None = None
_cache: dict[str, tuple[float, str | None]] = {}
_lock = threading.Lock()
TTL_SECONDS = 60.0


def register_local_lookup(func: Callable) -> None:
    """``func(db, enterprise_uuid) -> tenant_id | None`` (catalog service only)."""
    global _local
    _local = func


def _catalog_url() -> str:
    return os.environ.get("CATALOG_SERVICE_URL", "http://catalog:8000").rstrip("/")


def _remote(enterprise_id: str) -> str | None:
    url = f"{_catalog_url()}/internal/v1/enterprises/{enterprise_id}/tenant"
    headers = {"X-Internal-Api-Key": os.environ.get("INTERNAL_API_KEY", "")}
    rid = current_request_id()
    if rid and rid != "-":
        headers[REQUEST_ID_HEADER] = rid
    for attempt in range(3):
        try:
            r = httpx.get(url, headers=headers, timeout=httpx.Timeout(2.0))
            if r.status_code == 200:
                return (r.json() or {}).get("tenant_id")
            return None
        except RETRYABLE_EXCEPTIONS:
            time.sleep(0.1 * (attempt + 1))
        except httpx.HTTPError as exc:
            logger.warning("catalog tenant lookup failed: %s", type(exc).__name__)
            return None
    logger.warning("catalog tenant lookup: catalog unreachable")
    return None


def tenant_id_for_enterprise(db, enterprise_id) -> str | None:
    try:
        key = str(UUID(str(enterprise_id)))
    except (ValueError, TypeError):
        return None
    if _local is not None:
        return _local(db, UUID(key))
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < TTL_SECONDS:
            return hit[1]
    value = _remote(key)
    with _lock:
        _cache[key] = (now, value)
    return value
