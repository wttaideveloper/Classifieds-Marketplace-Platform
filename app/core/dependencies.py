from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.core.token_auth import (
    is_chat_scoped_token,
    resolve_chat_user_from_token_or_raise,
    resolve_user_from_token_or_raise,
)

bearer_scheme = HTTPBearer(auto_error=False, scheme_name="BearerAuth")

_CHAT_PATH_PREFIXES = (
    "/api/v1/conversations",
    "/api/v1/messages",
    "/api/v1/attachments",
    "/api/v1/notifications",
    "/api/v1/users",
    "/api/v1/devices",
    "/api/v1/providers",
    "/api/v1/subscriptions",
    "/api/v1/presence",
    "/api/v1/socket-io",
    "/api/v1/admin/chat",
)


def get_dev_user() -> dict:
    return {
        "id": settings.DEV_DEFAULT_USER_ID,
        "role": settings.DEV_DEFAULT_USER_ROLE,
        "email": "dev@localhost",
    }


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    token = credentials.credentials if credentials and credentials.credentials else None
    if not token:
        token = request.cookies.get(settings.WEB_SESSION_COOKIE_NAME)

    if not token:
        if settings.is_production:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )
        return get_dev_user()

    if is_chat_scoped_token(token):
        if not request.url.path.startswith(_CHAT_PATH_PREFIXES):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Chat-scoped token cannot access this endpoint",
            )
        return resolve_chat_user_from_token_or_raise(token)
    return resolve_user_from_token_or_raise(token)


def get_current_web_session_user(request: Request) -> dict:
    """Authenticate only from the HttpOnly cookie set by Web complete-login."""
    token = request.cookies.get(settings.WEB_SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Web session cookie is required",
        )
    return resolve_user_from_token_or_raise(token)


def require_roles(allowed_roles: list):
    def role_checker(current_user=Depends(get_current_user)):
        user_role = current_user.get("role")
        if user_role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Not authorized")
        return current_user

    return role_checker


def get_current_admin(current_user=Depends(get_current_user)):
    if current_user.get("role") != "admin":
        if not settings.is_production and current_user.get("id") == settings.DEV_DEFAULT_USER_ID:
            return {**current_user, "role": "admin"}
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user
