"""Synchronous client for /internal/v1 service-to-service APIs.

Most monolith domain code is synchronous and runs in FastAPI's thread pool, so internal calls are
blocking httpx calls with explicit timeouts:

* connect 2 s, read 10 s (overridable per call);
* a refused / timed-out *connection* is retried (up to 2 times) for every method - the request
  never reached the service, so nothing can have happened twice;
* anything after the request was sent (read timeout, 5xx) is NOT retried for POST/PATCH;
  GET/PUT/DELETE also retry on 502/503/504;
* the caller's X-Request-ID and the shared INTERNAL_API_KEY are always sent;
* failures raise ``ServiceUnavailable`` (a subclass of RuntimeError) so existing
  ``try/except Exception`` blocks in the domain code treat them like any other failure.
"""
from __future__ import annotations

import logging
import os
import time

import httpx

from mskit.correlation import REQUEST_ID_HEADER, current_request_id

logger = logging.getLogger(__name__)
_CONNECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
_IDEMPOTENT = {"GET", "HEAD", "PUT", "DELETE", "OPTIONS"}
_clients: dict[str, httpx.Client] = {}


class ServiceUnavailable(RuntimeError):
    pass


class ServiceError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"internal call failed with {status}: {body[:200]}")
        self.status = status
        self.body = body


def service_url(service: str) -> str:
    return os.environ.get(f"{service.upper()}_SERVICE_URL", f"http://{service}:8000").rstrip("/")


def _client(service: str) -> httpx.Client:
    base = service_url(service)
    client = _clients.get(base)
    if client is None:
        client = httpx.Client(base_url=base, timeout=httpx.Timeout(10.0, connect=2.0), trust_env=False)
        _clients[base] = client
    return client


def call(service: str, method: str, path: str, *, json=None, params=None, timeout: float | None = None, expect=(200, 201, 202, 204, 404), headers=None):
    method = method.upper()
    headers = {**(headers or {}), "X-Internal-Api-Key": os.environ.get("INTERNAL_API_KEY", "")}
    rid = current_request_id()
    if rid and rid != "-":
        headers[REQUEST_ID_HEADER] = rid
    kwargs = {"json": json, "params": params, "headers": headers}
    if timeout is not None:
        kwargs["timeout"] = httpx.Timeout(timeout, connect=2.0)
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            response = _client(service).request(method, "/internal/v1" + path, **kwargs)
        except _CONNECT_ERRORS as exc:
            last_exc = exc
            time.sleep(0.1 * (2 ** attempt))
            continue
        except httpx.HTTPError as exc:
            raise ServiceUnavailable(f"{service} {method} {path}: {type(exc).__name__}") from exc
        if response.status_code in (502, 503, 504) and method in _IDEMPOTENT and attempt < 2:
            time.sleep(0.1 * (2 ** attempt))
            continue
        if response.status_code not in expect:
            raise ServiceError(response.status_code, response.text)
        return response
    raise ServiceUnavailable(f"{service} unreachable for {method} {path}: {type(last_exc).__name__}")
