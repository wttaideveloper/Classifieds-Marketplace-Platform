import logging
from pathlib import Path

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
from app.services.attachment_storage import ensure_upload_directory

import app.realtime.events  # noqa: F401, E402 — register Socket.IO handlers

logger = logging.getLogger(__name__)

_API_DESCRIPTION = """
Classifieds Marketplace Platform REST API.

## Authentication

Protected endpoints require:
`Authorization: Bearer <tokens.access_token>`

**Invigorate auth (primary)**
1. Login: `POST https://p6wvqog202.execute-api.us-east-1.amazonaws.com/api/v1/auth/login`
2. Use `tokens.access_token` from response
3. Paste in Swagger **Authorize** button

| | |
|---|---|
| Issuer | `https://auth-dev.onruyl.com/realms/invigorate-healthcare` |
| Audience | `invigorate-api` |
| Algorithm | RS256 |
| User ID | JWT `sub` claim |

Full details: **Authentication** → `GET /api/v1/auth/integration`

**Local testing only:** `GET /api/v1/auth/dev-token` (requires `ENABLE_DEV_TOKEN=true`)

Server `.env` required:
```
KEYCLOAK_ISSUER=https://auth-dev.onruyl.com/realms/invigorate-healthcare
KEYCLOAK_AUDIENCE=invigorate-api
```

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


_notification_debug_logger = logging.getLogger("notification_debug")


@app.middleware("http")
async def notification_debug_status_middleware(request: Request, call_next):
    """TEMPORARY diagnostic: records the literal outbound HTTP status code for
    every /notifications* and /users/*/notifications request, so a silent
    frontend failure can't be mistaken for a backend one. Remove once the
    notification delivery investigation is closed."""
    response = await call_next(request)
    path = request.url.path
    if "notification" in path:
        _notification_debug_logger.info(
            "[NOTIF_DEBUG] HTTP %s %s -> status=%s",
            request.method, path, response.status_code,
        )
    return response


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
    upload_dir = ensure_upload_directory()
    logger.info("Chat attachment storage directory: %s", upload_dir)
    if settings.is_production and not Path(settings.UPLOAD_DIR).is_absolute():
        logger.warning(
            "UPLOAD_DIR is relative (%s). In production, set UPLOAD_DIR=/app/uploads "
            "and mount a persistent volume (e.g. -v /data/uploads:/app/uploads).",
            settings.UPLOAD_DIR,
        )
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
