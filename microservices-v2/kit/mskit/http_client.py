"""Timeouts and retry policy for calls between components (gateway -> service, service -> service).

Rules
-----
* Every call has explicit connect / read / write / pool timeouts (no "wait forever").
* A call is retried only when it is safe:
  - the method is idempotent by HTTP semantics (GET, HEAD, OPTIONS, PUT, DELETE) **and**
  - the failure happened before the request reached the service (connection refused /
    connect timeout / pool timeout), so the service cannot have acted on it.
  POST and PATCH are never retried: a duplicated payment, booking or message is worse than
  an error the client can see and retry deliberately. Read timeouts are never retried either:
  the service may already be working on the request.
* Failures are mapped to the monolith's error shape ``{"detail": "..."}``:
  connect failure -> 502, timeout -> 504.
"""
from __future__ import annotations

import asyncio
import os
import random
from dataclasses import dataclass

import httpx

IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
# Retried only while no byte of the request can have been processed by the service.
RETRYABLE_EXCEPTIONS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class CallPolicy:
    connect_timeout: float = 5.0
    # 300 s = the monolith's gunicorn timeout (large uploads, long polling).
    read_timeout: float = 300.0
    write_timeout: float = 300.0
    pool_timeout: float = 10.0
    retry_attempts: int = 2  # extra attempts after the first one
    retry_backoff: float = 0.2  # seconds, doubled each attempt, with jitter

    @classmethod
    def from_env(cls, prefix: str = "GATEWAY_") -> "CallPolicy":
        return cls(
            connect_timeout=_float_env(f"{prefix}CONNECT_TIMEOUT", cls.connect_timeout),
            read_timeout=_float_env(f"{prefix}READ_TIMEOUT", cls.read_timeout),
            write_timeout=_float_env(f"{prefix}WRITE_TIMEOUT", cls.write_timeout),
            pool_timeout=_float_env(f"{prefix}POOL_TIMEOUT", cls.pool_timeout),
            retry_attempts=int(_float_env(f"{prefix}RETRY_ATTEMPTS", cls.retry_attempts)),
            retry_backoff=_float_env(f"{prefix}RETRY_BACKOFF", cls.retry_backoff),
        )

    def timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_timeout,
            read=self.read_timeout,
            write=self.write_timeout,
            pool=self.pool_timeout,
        )


def is_retry_safe(method: str, exc: BaseException) -> bool:
    return method.upper() in IDEMPOTENT_METHODS and isinstance(exc, RETRYABLE_EXCEPTIONS)


async def backoff_sleep(policy: CallPolicy, attempt: int) -> None:
    delay = policy.retry_backoff * (2 ** attempt)
    await asyncio.sleep(delay + random.uniform(0, delay / 2))


def error_status_for(exc: BaseException) -> tuple[int, str]:
    """Map a transport failure to (status, detail) in the monolith's error format."""
    if isinstance(exc, httpx.TimeoutException) and not isinstance(exc, httpx.ConnectTimeout):
        return 504, "Upstream service timed out"
    return 502, "Upstream service unavailable"


class ServiceClient:
    """Small JSON client for explicit service-to-service APIs (propagates X-Request-ID)."""

    def __init__(self, base_url: str, policy: CallPolicy | None = None, transport=None):
        self.policy = policy or CallPolicy.from_env("SERVICE_CALL_")
        self._client = httpx.AsyncClient(base_url=base_url, timeout=self.policy.timeout(), transport=transport)

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        from mskit.correlation import REQUEST_ID_HEADER, current_request_id

        headers = dict(kwargs.pop("headers", None) or {})
        rid = current_request_id()
        if rid and rid != "-":
            headers.setdefault(REQUEST_ID_HEADER, rid)
        attempt = 0
        while True:
            try:
                return await self._client.request(method, url, headers=headers, **kwargs)
            except Exception as exc:  # noqa: BLE001
                if attempt < self.policy.retry_attempts and is_retry_safe(method, exc):
                    await backoff_sleep(self.policy, attempt)
                    attempt += 1
                    continue
                raise

    async def aclose(self) -> None:
        await self._client.aclose()
