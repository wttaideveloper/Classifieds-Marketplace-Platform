"""Builds a v2 service application.

Configured like the monolith's ``app/main.py`` so public responses keep their shape:
CORS, HTTPS link rewriting, the notification debug log, RFC 7807 problem handler,
``CustomException`` -> ``{"detail": ...}``, unhandled -> 500 ``{"detail": "Internal server error"}``.
Public routers are mounted under ``/api/v1`` with the monolith's prefixes, in the monolith's order.

Added: X-Request-ID correlation, ``/_service/health``, ``/_service/ready`` and internal APIs under
``/internal/v1`` (shared-key protected, never routed by the gateway).
"""
from __future__ import annotations

import hmac
import logging
import os
from contextlib import asynccontextmanager
from typing import Callable, Iterable, Sequence

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from mskit.correlation import CorrelationIdMiddleware
from mskit.custom_exception import CustomException
from mskit.logging_setup import configure_logging

API_PREFIX = "/api/v1"


def require_internal_key(x_internal_api_key: str | None = Header(default=None)) -> None:
    """Dependency for /internal/v1 routes: callers must present INTERNAL_API_KEY."""
    expected = os.environ.get("INTERNAL_API_KEY", "")
    if not expected or not x_internal_api_key or not hmac.compare_digest(expected, x_internal_api_key):
        raise HTTPException(status_code=403, detail="Internal API key required")


def create_service_app(
    *,
    name: str,
    public_routers: Sequence[tuple],  # (router, prefix, tags | None)
    internal_routers: Sequence = (),
    database=None,
    on_startup: Iterable[Callable] = (),
    description: str = "",
    asgi_wrapper=None,  # e.g. Socket.IO around the API (communication service)
):
    from mskit import problem as _problem
    from mskit.https_links import HttpsLinkRewriteMiddleware
    from mskit.settings import settings

    configure_logging(name, settings.LOG_LEVEL)
    logger = logging.getLogger(f"service.{name}")

    @asynccontextmanager
    async def lifespan(_app):
        for hook in on_startup:
            hook()
        logger.info("Service %s ready", name)
        yield

    app = FastAPI(
        title=f"Classifieds Marketplace — {name} service",
        version="2.0.0",
        description=description,
        docs_url="/_service/docs",
        redoc_url=None,
        openapi_url="/_service/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(HttpsLinkRewriteMiddleware)

    notification_debug_logger = logging.getLogger("notification_debug")

    @app.middleware("http")
    async def notification_debug_status_middleware(request: Request, call_next):
        response = await call_next(request)
        if "notification" in request.url.path:
            notification_debug_logger.info(
                "[NOTIF_DEBUG] HTTP %s %s -> status=%s", request.method, request.url.path, response.status_code
            )
        return response

    _problem.install(app)

    @app.exception_handler(CustomException)
    def custom_exception_handler(request, exc: CustomException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(Exception)
    def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    for router, prefix, tags in public_routers:
        kwargs = {"prefix": API_PREFIX + prefix}
        if tags is not None:
            kwargs["tags"] = list(tags)
        app.include_router(router, **kwargs)
    for router in internal_routers:
        app.include_router(router, prefix="/internal/v1")

    @app.get("/health", include_in_schema=False)
    def health():
        return {"status": "ok"}

    @app.get("/_service/health", include_in_schema=False)
    def service_health():
        return {"status": "ok", "service": name}

    @app.get("/_service/ready", include_in_schema=False)
    def service_ready():
        checks = {"database": database.ping()} if database is not None else {}
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse(status_code=200 if ok else 503, content={"status": "ok" if ok else "unavailable", "service": name, "checks": checks})

    outer = asgi_wrapper(app) if asgi_wrapper is not None else app
    return CorrelationIdMiddleware(outer), app
