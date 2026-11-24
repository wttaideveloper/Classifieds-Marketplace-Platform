"""Request correlation shared by the gateway and every service.

Every request carries an ``X-Request-ID``. The gateway accepts a well-formed id from the
client (or generates one), forwards it to the owning service, and the service puts it on
every log line written while handling that request. The id is echoed on the response so a
client, the gateway log and the service log can all be joined on it.
"""
from __future__ import annotations

import contextvars
import re
import uuid

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_HEADER_LOWER = REQUEST_ID_HEADER.lower().encode("latin-1")
# Accept caller-supplied ids only when they are short and printable, so a header cannot be
# used to inject text into log lines.
_VALID_ID = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def new_request_id() -> str:
    return uuid.uuid4().hex


def sanitize_request_id(value: str | None) -> str | None:
    if value and _VALID_ID.match(value):
        return value
    return None


def current_request_id() -> str:
    return request_id_var.get()


def _header(scope, name: bytes) -> str | None:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            try:
                return value.decode("latin-1")
            except Exception:  # pragma: no cover - defensive
                return None
    return None


class CorrelationIdMiddleware:
    """Pure ASGI middleware (works for HTTP and WebSocket, never buffers the body).

    * reads ``X-Request-ID`` from the request, or generates one;
    * exposes it to logging through ``request_id_var``;
    * adds it to the response headers when the response does not already carry one.
    """

    def __init__(self, app, *, echo_header: bool = True):
        self.app = app
        self.echo_header = echo_header

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        request_id = sanitize_request_id(_header(scope, _REQUEST_ID_HEADER_LOWER)) or new_request_id()
        token = request_id_var.set(request_id)
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id

        async def send_with_id(message):
            if self.echo_header and message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                if not any(k.lower() == _REQUEST_ID_HEADER_LOWER for k, _ in headers):
                    headers.append((_REQUEST_ID_HEADER_LOWER, request_id.encode("latin-1")))
                    message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)
