from fastapi import APIRouter, Body, HTTPException, status

from app.core.config import settings
from app.core.security import create_access_token
from app.schemas.auth_schema import (
    DEFAULT_DEV_USER_ID,
    DevTokenRequest,
    AuthIntegrationResponse,
    TEST_ADMIN_USER_ID,
    TEST_CUSTOMER_USER_ID,
    TEST_PROVIDER_USER_ID,
    TestUsersResponse,
    TokenResponse,
)
from app.services.auth_integration_service import get_auth_integration_info

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
            "Production auth: POST https://p6wvqog202.execute-api.us-east-1.amazonaws.com/api/v1/auth/login",
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
