from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

from app.core.config import settings
from app.core.dependencies import get_current_user, get_current_web_session_user
from app.core.security import create_access_token, create_chat_access_token
from app.core.token_auth import resolve_user_from_token
from app.schemas.auth_schema import (
    DEFAULT_DEV_USER_ID,
    DevTokenRequest,
    AuthIntegrationResponse,
    TenantListItem,
    TEST_ADMIN_USER_ID,
    TEST_CUSTOMER_USER_ID,
    TEST_PROVIDER_USER_ID,
    TestUsersResponse,
    TokenResponse,
    ChatTokenResponse,
)
from app.services.auth_integration_service import get_auth_integration_info
from app.services.invigorate_auth_client import list_tenants

router = APIRouter(tags=["Authentication"])


def _dev_token_enabled() -> bool:
    return (not settings.is_production) or settings.ENABLE_DEV_TOKEN


def _guard_dev_token():
    if not _dev_token_enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Dev tokens are not available. Set ENVIRONMENT=development "
                "or ENABLE_DEV_TOKEN=true on the server."
            ),
        )


def _issue_dev_token(data: DevTokenRequest | None = None) -> TokenResponse:
    payload_in = data or DevTokenRequest()
    user_id = str(payload_in.user_id or DEFAULT_DEV_USER_ID)
    email = payload_in.email or "provider@test.com"
    token_payload = {
        "id": user_id,
        "sub": user_id,
        "role": payload_in.role,
        "email": email,
    }
    token = create_access_token(token_payload)
    return TokenResponse(
        access_token=token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user={
            "id": user_id,
            "role": payload_in.role,
            "email": email,
        },
    )


@router.post(
    "/chat-token",
    response_model=ChatTokenResponse,
    summary="Issue Web Session Chat Token",
    description=(
        "Uses the HttpOnly Web session cookie to issue a chat-scoped token. "
        "The token is valid for a short period and may be renewed while the Web session remains active."
    ),
)
def issue_chat_token(
    current_user: dict = Depends(get_current_web_session_user),
) -> ChatTokenResponse:
    return ChatTokenResponse(
        access_token=create_chat_access_token(current_user),
        expires_in=settings.CHAT_TOKEN_EXPIRE_SECONDS,
        user_id=current_user["id"],
        tenant_id=current_user.get("tenant_id"),
    )


@router.get(
    "/integration",
    response_model=AuthIntegrationResponse,
    summary="Auth Integration Reference",
    description=(
        "Documents how to authenticate with the **Invigorate auth team** login API "
        "and use the returned JWT on this marketplace API. "
        "Use this as the Swagger reference for frontend integration."
    ),
)
def auth_integration_reference():
    return get_auth_integration_info()


@router.get(
    "/test-users",
    response_model=TestUsersResponse,
    summary="List Static Test User IDs",
    description=(
        "Reference IDs for **local dev tokens** and seeded chat conversations. "
        "For real users, login via Invigorate `POST /api/v1/auth/login` — see `GET /auth/integration`."
    ),
)
def list_test_users():
    return TestUsersResponse(
        admin_user_id=TEST_ADMIN_USER_ID,
        provider_user_id=TEST_PROVIDER_USER_ID,
        customer_user_id=TEST_CUSTOMER_USER_ID,
        recommended_for_admin_messages="provider",
        notes=[
            "Production auth: POST https://admin.apis.invigor8.app/api/v1/auth/login",
            "Use tokens.access_token as Authorization: Bearer <token> on this marketplace API.",
            "See GET /api/v1/auth/integration for full Invigorate auth details.",
            "Use GET /api/v1/auth/dev-token only for local/testing when ENABLE_DEV_TOKEN=true.",
            f"For /admin/messages testing, use role=provider and user_id={TEST_PROVIDER_USER_ID}.",
            "GET /api/v1/conversations/provider returns conversations for the token user.",
        ],
    )


@router.get(
    "/dev-token",
    response_model=TokenResponse,
    summary="Generate Default Development JWT (GET)",
    description=(
        "**Testing only.** Returns a local marketplace JWT (HS256), not an Invigorate auth token. "
        f"For production integration use Invigorate login — see `GET /auth/integration`. "
        f"Default user: `{TEST_PROVIDER_USER_ID}`."
    ),
)
def create_dev_token_get():
    _guard_dev_token()
    return _issue_dev_token(
        DevTokenRequest(
            user_id=TEST_PROVIDER_USER_ID,
            role="provider",
            email="provider@test.com",
        )
    )


@router.post(
    "/dev-token",
    response_model=TokenResponse,
    summary="Generate Development JWT (POST)",
    description=(
        "**Testing only.** Creates a local marketplace JWT (HS256) for Swagger/Socket.IO testing. "
        "For Invigorate auth use `POST /api/v1/auth/login` on the auth API — see `GET /auth/integration`."
    ),
)
def create_dev_token(payload: DevTokenRequest | None = Body(default=None)):
    _guard_dev_token()
    return _issue_dev_token(payload)


@router.get(
    "/tenants",
    response_model=list[TenantListItem],
    status_code=status.HTTP_200_OK,
    summary="List Tenants",
    description=(
        "Returns all tenants from the Invigorate Authentication service. "
        "Use the returned `id` as `tenant_id` when creating an Enterprise. "
        "Returns an empty list when `INVIGORATE_AUTH_BASE_URL` / `INVIGORATE_INTERNAL_API_KEY` are not configured."
    ),
)
def list_tenants_endpoint(
    current_user: dict = Depends(get_current_user),
) -> list[TenantListItem]:
    tenants = list_tenants()
    if tenants is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tenant service is currently unavailable. Please try again later.",
        )
    return [
        TenantListItem(id=t["id"], name=t["name"], slug=t["slug"])
        for t in tenants
        if t.get("id") and t.get("name") and t.get("slug")
    ]


@router.get(
    "/session",
    summary="Get Web Session (Frontend Auth Check)",
    description="Returns `authenticated` flag for frontend `GET /api/v1/auth/session` with `credentials: include`. Supports both HttpOnly session cookie and Bearer token.",
)
def get_session(request: Request):
    # Try cookie first, then Authorization header (same as get_current_user but non-throwing)
    token = request.cookies.get(settings.WEB_SESSION_COOKIE_NAME)
    if not token:
        auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
    # Also check Authorization via bearer_scheme header if present (case insensitive)
    user = resolve_user_from_token(token) if token else None
    # Dev fallback in non-production (mirrors get_current_user behavior)
    if not user and not settings.is_production:
        # If no token but dev mode, still consider authenticated as dev user for local testing
        # Frontend will receive authenticated:true with dev user
        from app.core.dependencies import get_dev_user

        # Only return dev user if token was missing but we are in dev; otherwise respect unauthenticated
        # To avoid masking real auth failures, only use dev user when token is None and dev mode
        if token is None:
            user = get_dev_user()

    if not user:
        # Return 200 with authenticated:false so frontend can handle gracefully (not 401)
        return {
            "authenticated": False,
            "message": "Not authenticated",
            "data": {"authenticated": False},
        }

    # Build frontend-compatible data: include both marketplace user fields and authenticated flag
    # Frontend logs showed data keys: id, email, fullName, phone, country, address, preferredLocale, emailVerified, groups, modules, userModules, membership, roles, impersonation, userId
    # We provide at least id/userId/email/role plus authenticated
    data = {
        "authenticated": True,
        "id": user.get("id"),
        "userId": user.get("id"),
        "email": user.get("email"),
        "role": user.get("role"),
        "roles": [user.get("role")] if user.get("role") else [],
        "tenant_id": user.get("tenant_id"),
        "tenantId": user.get("tenant_id"),
        # Pass through any extra claims that may exist from Keycloak
        **{k: v for k, v in user.items() if k not in ("id", "email", "role", "tenant_id")},
    }
    # Ensure fullName compatibility if only email exists
    if "fullName" not in data and user.get("email"):
        data["fullName"] = user.get("email").split("@")[0]

    return {
        "authenticated": True,
        "message": "Session active",
        "data": data,
    }
