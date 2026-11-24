"""API gateway: one base URL, the monolith's paths, each request forwarded to its owning service.

Run:  uvicorn gateway_app.main:app --host 0.0.0.0 --port 8000

Own endpoints (never forwarded):
* ``GET /_gateway/health`` - liveness of the gateway itself;
* ``GET /_gateway/ready``  - readiness of every service (``/_service/ready``).
``/_service/*`` and ``/internal/*`` paths are internal to the services and are answered 404 here.
The API documentation (``/docs``, ``/redoc``, ``/openapi.json`` and their ``/api/`` variants) is served
from a snapshot of the monolith's own OpenAPI document (``openapi.snapshot.json``), because no v2
service contains the whole API.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx

from gateway_app.config import GatewaySettings
from gateway_app.docs import DOCS_PATHS, DocsSnapshot
from gateway_app.proxy import HttpForwarder, elapsed_ms, forward_websocket, send_json_error
from gateway_app.routing import RouteTable, normalize_socketio_path
from mskit.correlation import CorrelationIdMiddleware
from mskit.logging_setup import configure_logging

logger = logging.getLogger("gateway")


class Gateway:
    def __init__(self, settings: GatewaySettings, table: RouteTable | None = None, transports: dict | None = None):
        self.settings = settings
        socketio_path = normalize_socketio_path(
            settings.socketio_path, environment=settings.environment, standalone=settings.socketio_standalone
        )
        self.table = table or RouteTable.load(socketio_path=socketio_path)
        missing = self.table.services - set(settings.service_urls)
        if missing:
            raise RuntimeError(f"No URL configured for services: {sorted(missing)}")
        self.forwarder = HttpForwarder(settings.service_urls, settings.policy, transports)
        self.docs = DocsSnapshot()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        path = scope.get("path", "/")
        if scope["type"] == "http":
            if path.startswith("/_gateway/"):
                await self._own_endpoint(scope, receive, send)
                return
            if path.startswith(("/_service/", "/internal/")):
                # service health and service-to-service APIs are never public
                await send_json_error(send, 404, "Not Found")
                return
            if path in DOCS_PATHS:
                await self.docs.serve(scope, send)
                return
        decision = self.table.resolve(path, scope.get("method", "GET"), scope["type"])
        start = time.perf_counter()
        if scope["type"] == "websocket":
            await forward_websocket(self.settings.service_urls[decision.service], scope, receive, send, self.settings.policy)
            logger.info("WS %s -> %s (%s) closed after %dms", path, decision.service, decision.kind, elapsed_ms(start))
            return
        status = await self.forwarder.forward(decision.service, scope, receive, send)
        logger.info(
            "%s %s -> %s [%s] %s %dms",
            scope["method"], path, decision.service, decision.kind, status, elapsed_ms(start),
        )

    async def _lifespan(self, receive, send):
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                logger.info("Gateway started: %d routes, services=%s", len(self.table.entries), sorted(self.table.services))
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await self.forwarder.aclose()
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _own_endpoint(self, scope, receive, send):
        path = scope["path"]
        if scope["method"] not in ("GET", "HEAD"):
            await send_json_error(send, 405, "Method Not Allowed")
            return
        if path == "/_gateway/health":
            await _send_json(send, 200, {"status": "ok", "service": "gateway"})
        elif path == "/_gateway/ready":
            results = await self._check_services()
            ok = all(r == "ok" for r in results.values())
            await _send_json(send, 200 if ok else 503, {"status": "ok" if ok else "degraded", "services": results})
        else:
            await send_json_error(send, 404, "Not Found")

    async def _check_services(self) -> dict[str, str]:
        async def one(name: str) -> tuple[str, str]:
            try:
                r = await self.forwarder.client_for(name).get("/_service/ready", timeout=3.0)
                return name, "ok" if r.status_code == 200 else f"status {r.status_code}"
            except httpx.HTTPError as exc:
                return name, f"error: {type(exc).__name__}"

        pairs = await asyncio.gather(*(one(n) for n in sorted(self.table.services)))
        return dict(pairs)


async def _send_json(send, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
    })
    await send({"type": "http.response.body", "body": body})


def create_app(settings: GatewaySettings | None = None, *, table: RouteTable | None = None, transports: dict | None = None):
    settings = settings or GatewaySettings.from_env()
    configure_logging("gateway", settings.log_level)
    gateway = Gateway(settings, table=table, transports=transports)
    return CorrelationIdMiddleware(gateway, echo_header=settings.echo_request_id)


app = create_app()
