"""Catalog access policy shared by every service (copied from app/core/catalog_access.py).

Only the claim-based part lives here. Query scoping and write validation join the enterprises
table, so they stay in the catalog service. Tenant resolution's enterprise fallback goes through
mskit.auth.tenant_lookup (catalog's database in catalog, its internal API elsewhere).
"""
import logging
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from mskit.auth.auth_context import resolve_auth_tenant_id_with_db
from mskit.settings import settings
from mskit.auth.dependencies import bearer_scheme, get_current_user, get_web_session_cookie_token
from mskit.auth.super_admin_identity import profile_status_is_active

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CatalogAccess:
    role: str
    tenant_id: UUID | None = None
    provider_user_id: UUID | None = None


def get_optional_catalog_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)):
    # Keep existing public browsing. Supplied invalid credentials must fail auth.
    if credentials is None and not get_web_session_cookie_token(request):
        return None
    return get_current_user(request, credentials)


from mskit.auth.roles import resolve_catalog_role  # noqa: E402,F401 - re-exported


def get_catalog_access(request: Request, user=Depends(get_optional_catalog_user)):
    if user is None:
        return CatalogAccess("public")
    if not profile_status_is_active(user):
        raise HTTPException(403, "Inactive user")
    role = resolve_catalog_role(user)
    if role == "super_admin":
        return CatalogAccess(role)
    if role == "customer":
        return CatalogAccess(role)
    if role is None:
        logger.info(
            "Catalog access denied: unrecognized application role | path=%s user_id=%s "
            "role=%s tenant_role=%s user_role=%s",
            request.url.path, user.get("id"), user.get("role"), user.get("tenant_role"), user.get("user_role"),
        )
        if settings.CATALOG_UNRECOGNIZED_ROLE_READ_ONLY:
            # Opt-in: a logged-in user with no recognised role browses exactly what an anonymous visitor
            # already can (read-only, unscoped). Writes still need a real role (require_catalog_writer).
            return CatalogAccess("customer")
        raise HTTPException(403, "Catalog access denied")
    authorization = request.headers.get("authorization", "")
    token = authorization.split(" ", 1)[1] if authorization.lower().startswith("bearer ") else get_web_session_cookie_token(request)
    tenant = resolve_auth_tenant_id_with_db(None, user, access_token=token)
    try:
        tenant_id = UUID(str(tenant))
        provider_id = UUID(str(user.get("id"))) if role == "provider" else None
    except (ValueError, TypeError):
        logger.info(
            "Catalog access denied: no resolvable tenant id | path=%s user_id=%s role=%s",
            request.url.path, user.get("id"), role,
        )
        raise HTTPException(403, "Authenticated tenant/user identity required")
    return CatalogAccess(role, tenant_id, provider_id)


def require_catalog_writer(access: CatalogAccess = Depends(get_catalog_access)):
    if access.role == "public":
        raise HTTPException(401, "Not authenticated")
    if access.role not in ("admin", "super_admin"):
        raise HTTPException(403, "Providers are read-only; Enterprise Admin access required")
    return access


def require_review_moderator(access: CatalogAccess = Depends(get_catalog_access)):
    """Only a Super Admin changes the status of a review; Enterprise Admins and providers can look but not moderate."""
    if access.role == "public":
        raise HTTPException(401, "Not authenticated")
    if access.role != "super_admin":
        raise HTTPException(403, "Only a Super Admin can moderate reviews")
    return access


