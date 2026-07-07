from uuid import uuid4

from fastapi import APIRouter, Body, HTTPException, status

from app.core.config import settings
from app.core.security import create_access_token
from app.schemas.auth_schema import (
    DEFAULT_DEV_USER_ID,
    DevTokenRequest,
    TokenResponse,
)

router = APIRouter(tags=["Authentication"])


def _issue_dev_token(data: DevTokenRequest) -> TokenResponse:
    user_id = str(data.user_id or DEFAULT_DEV_USER_ID)
    email = str(data.email or "dev@localhost")
    payload = {
        "id": user_id,
        "sub": user_id,
        "role": data.role,
        "email": email,
    }
    token = create_access_token(payload)
    return TokenResponse(
        access_token=token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user={
            "id": user_id,
            "role": data.role,
            "email": email,
        },
    )


@router.post(
    "/dev-token",
    response_model=TokenResponse,
    summary="Generate Development JWT",
    description=(
        "Creates a JWT for local Swagger and Socket.IO testing. "
        "**Available only when `ENVIRONMENT=development`.** "
        "Copy `access_token` into Swagger **Authorize** as: `Bearer <token>`"
    ),
)
def create_dev_token(payload: DevTokenRequest = Body(default_factory=DevTokenRequest)):
    if settings.is_production:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dev tokens are not available in production",
        )
    return _issue_dev_token(payload)


@router.get(
    "/dev-token",
    response_model=TokenResponse,
    summary="Generate Default Development JWT (GET)",
    description=(
        "Quick dev token with default user. Same as POST `/auth/dev-token` with defaults. "
        "Use for fast Swagger testing."
    ),
)
def create_dev_token_get():
    if settings.is_production:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dev tokens are not available in production",
        )
    return _issue_dev_token(DevTokenRequest())
