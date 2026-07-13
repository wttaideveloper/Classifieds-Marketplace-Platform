from fastapi import FastAPI
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

BEARER_AUTH_SCHEME = "BearerAuth"

BEARER_AUTH_DESCRIPTION = """JWT **access token** for authenticated API requests.

**Quick start (development / staging):**
1. Call `GET /api/v1/auth/dev-token` (no body required) when `ENVIRONMENT=development` or `ENABLE_DEV_TOKEN=true`.
2. Copy `access_token` from the response.
3. Click **Authorize** above and paste the token — Swagger adds `Bearer ` automatically.

**Provider web / admin messages:** use `GET /api/v1/auth/test-users` for static IDs, or POST `/api/v1/auth/dev-token` with:
```json
{ "user_id": "550e8400-e29b-41d4-a716-446655440020", "role": "provider" }
```

**Production:** use your standard login flow when available; until then enable `ENABLE_DEV_TOKEN=true` on staging servers."""

OPENAPI_TAGS: list[dict[str, str]] = [
    {
        "name": "Authentication",
        "description": "JWT dev tokens and static test user IDs for Swagger, web, and Socket.IO.",
    },
    {
        "name": "System",
        "description": "Health check and API inventory.",
    },
    {
        "name": "Conversations",
        "description": (
            "Create and list conversations, archive/close/reopen, provider inbox "
            "(`other_participant_user_id` for presence), and conversation messages."
        ),
    },
    {
        "name": "Messages",
        "description": "Send, edit, delete, search, and mark messages read.",
    },
    {
        "name": "Attachments",
        "description": "Upload, download, delete, and transcribe chat file attachments.",
    },
    {
        "name": "Chat Notifications",
        "description": (
            "Unread badge counts, notification history, mark-read endpoints, and user preferences "
            "for **Email Notifications**, **Push/App Notifications** (Firebase FCM), "
            "and **SMS/Text Notifications** (Bravo). "
            "Register device tokens under **Devices** for Firebase push delivery."
        ),
    },
    {
        "name": "Presence",
        "description": "Online/offline status — pair with `other_participant_user_id` on provider conversation lists.",
    },
    {
        "name": "Typing Indicator",
        "description": "REST fallback for typing state (real-time via Socket.IO).",
    },
    {
        "name": "Socket.IO",
        "description": "Connection info, event catalog, and REST test helpers for real-time chat.",
    },
    {
        "name": "Chat Administration",
        "description": "Admin dashboards, exports, and archived/closed conversation views.",
    },
    {
        "name": "Provider Assignment",
        "description": "Assign or look up the provider on a conversation.",
    },
    {
        "name": "Chat Subscriptions",
        "description": "Preview-chat eligibility and monthly message limits.",
    },
    {
        "name": "Devices",
        "description": (
            "Register **Firebase Cloud Messaging (FCM)** device tokens for Push/App Notifications. "
            "Call `POST /devices/register` after the Firebase SDK provides a token. "
            "Pair with `push_enabled` on `PUT /notifications/preferences`."
        ),
    },
    {
        "name": "Enterprise",
        "description": "Enterprise CRUD and listing.",
    },
    {
        "name": "Enterprise Locations",
        "description": "Locations under an enterprise.",
    },
    {
        "name": "Products",
        "description": "Product catalog management.",
    },
    {
        "name": "Services",
        "description": "Service catalog management.",
    },
    {
        "name": "Dynamic Attributes",
        "description": "Custom attribute definitions for entities.",
    },
    {
        "name": "Dynamic Attributes (Legacy)",
        "description": "Backward-compatible `/attributes` alias.",
    },
    {
        "name": "Search",
        "description": "Search enterprises, products, and services.",
    },
    {
        "name": "Onboarding Forms",
        "description": "Onboarding form templates — create, publish, duplicate.",
    },
]

SWAGGER_UI_PARAMETERS: dict = {
    "persistAuthorization": True,
    "displayRequestDuration": True,
    "filter": True,
    "docExpansion": "none",
    "tryItOutEnabled": True,
}

# Routes that do not require a token (OpenAPI `security: []`).
PUBLIC_OPERATIONS: set[tuple[str, str]] = {
    ("get", "/api/v1/auth/test-users"),
    ("get", "/api/v1/auth/dev-token"),
    ("post", "/api/v1/auth/dev-token"),
    ("get", "/api/v1/notifications/channels"),
    ("get", "/api/v1/health"),
    ("get", "/api/v1/inventory"),
    ("get", "/health"),
}


def configure_openapi(app: FastAPI) -> None:
    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )

        schema["tags"] = OPENAPI_TAGS

        components = schema.setdefault("components", {})
        components["securitySchemes"] = {
            BEARER_AUTH_SCHEME: {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": BEARER_AUTH_DESCRIPTION,
            }
        }
        schema["security"] = [{BEARER_AUTH_SCHEME: []}]

        for path, path_item in schema.get("paths", {}).items():
            for method, operation in path_item.items():
                if method not in {"get", "post", "put", "patch", "delete", "options", "head"}:
                    continue
                if (method.lower(), path) in PUBLIC_OPERATIONS:
                    operation["security"] = []
                else:
                    operation.setdefault("security", [{BEARER_AUTH_SCHEME: []}])

        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi


def register_docs_routes(app: FastAPI) -> None:
    """Serve Swagger/ReDoc/OpenAPI at both root and /api/* paths."""

    @app.get("/docs", include_in_schema=False)
    @app.get("/api/docs", include_in_schema=False)
    async def swagger_ui():
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=f"{app.title} - Swagger UI",
            swagger_ui_parameters=SWAGGER_UI_PARAMETERS,
        )

    @app.get("/redoc", include_in_schema=False)
    @app.get("/api/redoc", include_in_schema=False)
    async def redoc_ui():
        return get_redoc_html(
            openapi_url="/openapi.json",
            title=f"{app.title} - ReDoc",
        )

    @app.get("/openapi.json", include_in_schema=False)
    @app.get("/api/openapi.json", include_in_schema=False)
    async def openapi_json():
        return JSONResponse(app.openapi())
