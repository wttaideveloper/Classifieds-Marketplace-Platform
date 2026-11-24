"""HTTP and WebSocket forwarding.

HTTP
* request and response bodies are streamed (300 MB training videos, Socket.IO long polling,
  file downloads) - nothing is buffered except small bodies of retry-safe methods;
* the client's ``Host`` header is forwarded unchanged and ``X-Forwarded-For/-Proto/-Host`` are
  set, so services (run with ``--forwarded-allow-ips``) see the same ``request.base_url``,
  scheme and client address as the monolith did (links such as payment/meeting callbacks are
  built from ``request.base_url``);
* response status, headers (including several ``Set-Cookie``) and bytes are passed through
  unchanged (``Content-Encoding`` is not decoded);
* transport failures -> 502/504 with the monolith's error shape ``{"detail": "..."}``.

WebSocket
* used for Socket.IO's websocket transport: the gateway opens the same URL on the
  communication service with the client's cookies, Authorization, Origin and sub-protocols,
  then relays frames both ways until either side closes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from urllib.parse import quote

import httpx

from mskit.correlation import REQUEST_ID_HEADER, current_request_id
from mskit.http_client import CallPolicy, backoff_sleep, error_status_for, is_retry_safe

logger = logging.getLogger("gateway.proxy")

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "trailers", "transfer-encoding", "upgrade",
}
# Small bodies of these methods are buffered so a refused connection can be retried safely.
_BUFFER_FOR_RETRY = {"GET", "HEAD", "OPTIONS", "DELETE"}
_MAX_BUFFERED_BODY = 1024 * 1024


def _connection_tokens(headers: list[tuple[bytes, bytes]]) -> set[str]:
    tokens: set[str] = set()
    for k, v in headers:
        if k.lower() == b"connection":
            tokens |= {t.strip().lower() for t in v.decode("latin-1").split(",") if t.strip()}
    return tokens


def _client_ip(scope) -> str | None:
    client = scope.get("client")
    return client[0] if client else None


def build_upstream_headers(scope, request_id: str) -> list[tuple[str, str]]:
    raw = list(scope.get("headers") or [])
    drop = HOP_BY_HOP | _connection_tokens(raw)
    out: list[tuple[str, str]] = []
    xff = None
    have_proto = have_host = False
    host_value = None
    for k, v in raw:
        name = k.decode("latin-1").lower()
        value = v.decode("latin-1")
        if name in drop or name == REQUEST_ID_HEADER.lower():
            continue
        if name == "x-forwarded-for":
            xff = value
            continue
        if name == "x-forwarded-proto":
            have_proto = True
        if name == "x-forwarded-host":
            have_host = True
        if name == "host":
            host_value = value
        out.append((name, value))
    ip = _client_ip(scope)
    if ip:
        out.append(("x-forwarded-for", f"{xff}, {ip}" if xff else ip))
    elif xff:
        out.append(("x-forwarded-for", xff))
    if not have_proto:
        scheme = scope.get("scheme", "http")
        out.append(("x-forwarded-proto", {"ws": "http", "wss": "https"}.get(scheme, scheme)))
    if not have_host and host_value:
        out.append(("x-forwarded-host", host_value))
    out.append((REQUEST_ID_HEADER.lower(), request_id))
    return out


def upstream_target(scope) -> str:
    raw_path = scope.get("raw_path")
    path = raw_path.decode("latin-1") if raw_path else quote(scope.get("path", "/"))
    query = scope.get("query_string") or b""
    return path + ("?" + query.decode("latin-1") if query else "")


async def send_json_error(send, status: int, detail: str, request_id: str | None = None) -> None:
    body = json.dumps({"detail": detail}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class HttpForwarder:
    def __init__(self, service_urls: dict[str, str], policy: CallPolicy, transports: dict | None = None):
        self.service_urls = service_urls
        self.policy = policy
        self._transports = transports or {}
        self._clients: dict[str, httpx.AsyncClient] = {}

    def client_for(self, service: str) -> httpx.AsyncClient:
        client = self._clients.get(service)
        if client is None:
            kwargs = {
                "base_url": self.service_urls[service],
                "timeout": self.policy.timeout(),
                "follow_redirects": False,
                "trust_env": False,
                "limits": httpx.Limits(max_connections=200, max_keepalive_connections=50),
            }
            if service in self._transports:
                kwargs["transport"] = self._transports[service]
            client = httpx.AsyncClient(**kwargs)
            self._clients[service] = client
        return client

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()

    async def forward(self, service: str, scope, receive, send) -> int:
        method = scope["method"].upper()
        request_id = current_request_id()
        headers = build_upstream_headers(scope, request_id)
        target = upstream_target(scope)
        client = self.client_for(service)

        retry_allowed = self.policy.retry_attempts > 0 and method in _BUFFER_FOR_RETRY
        body: bytes | None = None
        if retry_allowed:
            chunks, size, more = [], 0, True
            while more:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return 499
                chunk = message.get("body", b"")
                size += len(chunk)
                chunks.append(chunk)
                more = message.get("more_body", False)
                if size > _MAX_BUFFERED_BODY:
                    break
            body = b"".join(chunks)
            if more:  # unusually large body: stream the rest, no retry
                retry_allowed = False
                first = body

                async def rest():
                    yield first
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        yield message.get("body", b"")
                        if not message.get("more_body", False):
                            return

                content = rest()
            else:
                content = body
        else:

            async def stream():
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if chunk:
                        yield chunk
                    if not message.get("more_body", False):
                        return

            content = stream()

        attempt = 0
        while True:
            try:
                request = client.build_request(method, target, headers=headers, content=content)
                response = await client.send(request, stream=True)
                break
            except Exception as exc:  # noqa: BLE001
                if retry_allowed and attempt < self.policy.retry_attempts and is_retry_safe(method, exc):
                    logger.warning("retrying %s %s on %s after %s (attempt %d)", method, scope["path"], service, type(exc).__name__, attempt + 1)
                    await backoff_sleep(self.policy, attempt)
                    attempt += 1
                    continue
                status, detail = error_status_for(exc)
                logger.error("upstream %s failed for %s %s: %s", service, method, scope["path"], type(exc).__name__)
                await send_json_error(send, status, detail)
                return status

        try:
            out_headers = [
                (k, v) for k, v in response.headers.raw
                if k.decode("latin-1").lower() not in HOP_BY_HOP
            ]
            await send({"type": "http.response.start", "status": response.status_code, "headers": out_headers})
            if response.is_stream_consumed:  # already-buffered response (e.g. an in-memory transport)
                await send({"type": "http.response.body", "body": response.content, "more_body": True})
            else:
                async for chunk in response.aiter_raw():
                    if chunk:
                        await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except (httpx.TransportError, httpx.StreamError) as exc:
            # Headers are already sent: the only honest thing left is to cut the response short.
            logger.error("upstream %s broke mid-response for %s %s: %s", service, method, scope["path"], type(exc).__name__)
            raise
        finally:
            await response.aclose()
        return response.status_code


_WS_FORWARD_HEADERS = {
    "cookie", "authorization", "user-agent", "accept-language",
    "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "x-request-id",
}


async def forward_websocket(service_url: str, scope, receive, send, policy: CallPolicy) -> None:
    """Relay one WebSocket connection to ``service_url`` (http(s)://host:port)."""
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    message = await receive()
    if message["type"] != "websocket.connect":
        return
    base = service_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    url = base + upstream_target(scope)
    request_id = current_request_id()
    headers = [(k, v) for k, v in build_upstream_headers(scope, request_id) if k in _WS_FORWARD_HEADERS]
    origin = None
    subprotocols = None
    for k, v in scope.get("headers") or []:
        name = k.decode("latin-1").lower()
        if name == "origin":
            origin = v.decode("latin-1")
        elif name == "sec-websocket-protocol":
            subprotocols = [p.strip() for p in v.decode("latin-1").split(",") if p.strip()]
    try:
        upstream = await connect(
            url,
            additional_headers=headers,
            origin=origin,
            subprotocols=subprotocols,
            open_timeout=policy.connect_timeout,
            ping_interval=None,  # Engine.IO runs its own heartbeat
            max_size=None,
            compression=None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("websocket upstream refused %s: %s", scope.get("path"), type(exc).__name__)
        await send({"type": "websocket.close", "code": 1011})
        return

    await send({"type": "websocket.accept", "subprotocol": upstream.subprotocol})

    async def client_to_upstream():
        while True:
            msg = await receive()
            if msg["type"] == "websocket.receive":
                if msg.get("text") is not None:
                    await upstream.send(msg["text"])
                elif msg.get("bytes") is not None:
                    await upstream.send(msg["bytes"])
            elif msg["type"] == "websocket.disconnect":
                await upstream.close(code=msg.get("code", 1000) or 1000)
                return

    async def upstream_to_client():
        try:
            async for data in upstream:
                if isinstance(data, str):
                    await send({"type": "websocket.send", "text": data})
                else:
                    await send({"type": "websocket.send", "bytes": data})
            code = upstream.close_code or 1000
        except ConnectionClosed as exc:
            code = exc.code or 1000
        try:
            await send({"type": "websocket.close", "code": code})
        except Exception:  # noqa: BLE001 - client already gone
            pass

    tasks = [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            if t.exception() and not isinstance(t.exception(), ConnectionClosed):
                logger.warning("websocket relay ended with %s", type(t.exception()).__name__)
    finally:
        await upstream.close()


def elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)
