from fastapi import APIRouter, Body, HTTPException, status

from app.core.config import settings
from app.core.security import create_access_token
from app.schemas.auth_schema import (
    DEFAULT_DEV_USER_ID,
    DevTokenRequest,
    TEST_ADMIN_USER_ID,
    TEST_CUSTOMER_USER_ID,
    TEST_PROVIDER_USER_ID,
    TestUsersResponse,
    TokenResponse,
)

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
    "/test-users",
    response_model=TestUsersResponse,
    summary="List Static Test User IDs",
    description="Reference IDs for dev tokens and seeded chat conversations.",
)
def list_test_users():
    return TestUsersResponse(
        admin_user_id=TEST_ADMIN_USER_ID,
        provider_user_id=TEST_PROVIDER_USER_ID,
        customer_user_id=TEST_CUSTOMER_USER_ID,
        recommended_for_admin_messages="provider",
        notes=[
            "Use GET /api/v1/auth/dev-token for a default provider token (no body required).",
            "POST /api/v1/auth/dev-token body is optional — omit it or send {}.",
            f"For /admin/messages, use role=provider and user_id={TEST_PROVIDER_USER_ID}.",
            "GET /api/v1/conversations/provider returns conversations for the token user.",
            "Run scripts/seed_chat.py to create sample conversations on the server DB.",
        ],
    )


@router.get(
    "/dev-token",
    response_model=TokenResponse,
    summary="Generate Default Development JWT (GET)",
    description=(
        "Returns a provider JWT using static test user "
        f"`{TEST_PROVIDER_USER_ID}`. No request body required."
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
        "Creates a JWT for Swagger, web, and Socket.IO testing. "
        "Request body is **optional** — send `{}` or omit fields for defaults. "
        "Available when `ENVIRONMENT=development` or `ENABLE_DEV_TOKEN=true`."
    ),
)
def create_dev_token(payload: DevTokenRequest | None = Body(default=None)):
    _guard_dev_token()
    return _issue_dev_token(payload)
