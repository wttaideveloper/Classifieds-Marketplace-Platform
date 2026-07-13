import logging

import socketio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.core.openapi import OPENAPI_TAGS, configure_openapi, register_docs_routes
from app.db.database import Base, engine
from app.api.v1.router import api_router
from app.exceptions.custom_exception import CustomException
from app.realtime.server import SOCKETIO_PATH, sio

import app.realtime.events  # noqa: F401, E402 — register Socket.IO handlers

logger = logging.getLogger(__name__)

_API_DESCRIPTION = """
Classifieds Marketplace Platform REST API.

## Authentication

Protected endpoints require a **JWT Bearer token**.

1. Call `GET /api/v1/auth/dev-token` (development/staging with `ENABLE_DEV_TOKEN=true`).
2. Copy `access_token` from the response.
3. Click **Authorize** (top right) and paste the token.

See `GET /api/v1/auth/test-users` for static test user IDs.

## Quick reference

| Area | Key endpoints |
|------|----------------|
| Provider inbox | `GET /api/v1/conversations/provider` |
| Presence | `GET /api/v1/presence/online` + `other_participant_user_id` on provider list |
| Badge count | `GET /api/v1/notifications/unread-count` |
| Mark read | `PATCH /api/v1/conversations/{id}/read` |
| Real-time chat | Socket.IO — see **Socket.IO** tag |

## Notification channels

| UI setting | API | Provider |
|------------|-----|----------|
| Email Notifications | `email_enabled` | SMTP |
| Push/App Notifications | `push_enabled` + `POST /devices/register` | **Firebase FCM** |
| SMS/Text Notifications | `sms_enabled` + `sms_phone_number` | **Bravo SMS** |
| In-app badge | `in_app_enabled` | Platform |

See **Chat Notifications** → `GET /notifications/channels` for full reference.

## Test users

| User | ID | Use for |
|------|----|---------|
| Provider | `550e8400-e29b-41d4-a716-446655440020` | Provider web / `/admin/messages` |
| Customer | `550e8400-e29b-41d4-a716-446655440030` | Customer chat UI |
| Admin | `550e8400-e29b-41d4-a716-446655440000` | Admin dashboards |

Seed sample data: `python scripts/seed_chat.py`
"""

app = FastAPI(
    title="Classifieds Marketplace Platform API",
    version="1.0.0",
    description=_API_DESCRIPTION,
    openapi_tags=OPENAPI_TAGS,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

configure_openapi(app)
register_docs_routes(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(CustomException)
def custom_exception_handler(request, exc: CustomException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(Exception)
def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(
        "Unhandled error on %s %s",
        request.method,
        request.url.path,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.on_event("startup")
def startup():
    redis_url = settings.SOCKETIO_REDIS_URL.strip()
    if settings.WEB_CONCURRENCY > 1:
        logger.info(
            "Multi-worker mode: WEB_CONCURRENCY=%s redis=%s",
            settings.WEB_CONCURRENCY,
            redis_url or "MISSING",
        )
    logger.info("Socket.IO mounted at %s (socket_app entrypoint)", SOCKETIO_PATH)
    if not settings.is_production and settings.AUTO_CREATE_TABLES:
        try:
            Base.metadata.create_all(bind=engine)
            logger.info("Database connected and tables verified.")
        except OperationalError:
            logger.warning(
                "Database not reachable — start PostgreSQL with: docker compose up -d db"
            )


app.include_router(api_router, prefix="/api/v1")


socket_app = socketio.ASGIApp(
    sio,
    other_asgi_app=app,
    socketio_path=SOCKETIO_PATH,
)


@app.get("/health")
def health():
    return {"status": "ok"}
