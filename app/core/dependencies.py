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


def get_web_session_cookie_token(request: Request) -> str | None:
    """Read the WebAuth session token from cookies, trying the configured
    primary cookie name first, then any configured fallback names — covers a
    WebAuth provider issuing the session under a different cookie name."""
    for name in settings.web_session_cookie_names:
        value = request.cookies.get(name)
        if value:
            return value
    return None


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    token = credentials.credentials if credentials and credentials.credentials else None
    if not token:
        token = get_web_session_cookie_token(request)

    if not token:
        if settings.is_production or not settings.ENABLE_DEV_TOKEN:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )
        return get_dev_user()

    if is_chat_scoped_token(token):
        if not any(request.url.path == p or request.url.path.startswith(p + "/") for p in _CHAT_PATH_PREFIXES):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Chat-scoped token cannot access this endpoint",
            )
        return resolve_chat_user_from_token_or_raise(token)
    return resolve_user_from_token_or_raise(token)


def extract_access_token(request: Request) -> str | None:
    """Bearer token from the Authorization header, else the WebAuth session cookie.

    Needed by tenant resolution (``resolve_auth_tenant_id_with_db``), which can fall
    back to a live tenant lookup with the caller's own token.
    """
    authorization = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    if authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if token:
            return token
    return get_web_session_cookie_token(request)


def get_optional_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict | None:
    """Authenticate when a token is present, otherwise (or on any auth failure) return None.

    For endpoints that are legitimately public but must widen what an authenticated
    caller may see (e.g. staff seeing their own draft events). An expired or invalid
    token is treated as anonymous rather than raising, so a public endpoint that never
    required a token keeps working for clients that still send a stale one.
    """
    token = credentials.credentials if credentials and credentials.credentials else None
    if not token:
        token = get_web_session_cookie_token(request)
    if not token:
        return None
    try:
        return get_current_user(request, credentials)
    except HTTPException:
        return None


def get_current_web_session_user(request: Request) -> dict:
    """Authenticate only from the HttpOnly cookie set by Web complete-login."""
    token = get_web_session_cookie_token(request)
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
    """Requires admin OR super_admin role. Used for enterprise-level admin operations."""
    if current_user.get("role") not in ("admin", "super_admin"):
        if not settings.is_production and current_user.get("id") == settings.DEV_DEFAULT_USER_ID:
            return {**current_user, "role": "admin"}
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def get_current_super_admin(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Platform Super Admin for approval / reject / request-changes / audits.

    Same Keycloak/Bearer (or WebAuth cookie) validation as Event list/detail via
    ``get_current_user``, then resolves Platform Super Admin identity:

    authenticated token → subject → internal user / claims
    → ``isSuperAdmin == true`` and ``status == active`` → allow

    Does **not** require Enterprise tenancy. Enterprise Admin (`admin`) remains
    allowed for backwards-compatible testing of shared admin tools.
    """
    from app.services.super_admin_identity import resolve_platform_super_admin_user

    token = None
    credentials_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if credentials_header and credentials_header.lower().startswith("bearer "):
        token = credentials_header.split(" ", 1)[1].strip()
    if not token:
        token = get_web_session_cookie_token(request)

    resolved = resolve_platform_super_admin_user(current_user, access_token=token)
    if resolved:
        return resolved

    # Backwards-compatible: Enterprise Admin (`admin`) for shared testing flows
    if current_user.get("role") == "admin":
        return current_user

    if not settings.is_production and current_user.get("id") == settings.DEV_DEFAULT_USER_ID:
        return {**current_user, "role": "admin"}

    raise HTTPException(status_code=403, detail="Super Admin access required")


def require_event_form_builder_admin(current_user=Depends(get_current_user)):
    """Event form builder — same auth as Events (Bearer OR WebAuth cookie via get_current_user).

    Allows admin, super_admin, and provider (Invigorate tenant_admin/internal_user → provider).
    Use this instead of get_current_super_admin until a dedicated super-admin role exists.
    """
    role = current_user.get("role")
    if role in ("admin", "super_admin", "provider"):
        return current_user
    if not settings.is_production and current_user.get("id") == settings.DEV_DEFAULT_USER_ID:
        return {**current_user, "role": "admin"}
    raise HTTPException(status_code=403, detail="Event form builder access required (admin or provider)")


def require_form_configuration_super_admin(current_user=Depends(get_current_super_admin)):
    """Strict management guard: excludes shared-admin and development fallbacks."""
    if current_user.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Super Admin access required")
    return current_user
