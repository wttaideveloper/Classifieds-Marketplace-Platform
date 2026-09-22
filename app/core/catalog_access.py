"""Shared Product/Service access policy, derived only from authenticated claims."""
import logging
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.auth_context import resolve_auth_tenant_id_with_db
from app.core.dependencies import bearer_scheme, get_current_user, get_web_session_cookie_token
from app.db.database import get_db
from app.models.enterprise_model import Enterprise
from app.services.super_admin_identity import profile_status_is_active

logger = logging.getLogger(__name__)


def _safe_db_target(db: Session) -> str:
    """Host/dbname only — never credentials. Used solely to confirm two
    requests hit the same database instance during the empty-result
    investigation."""
    try:
        from app.core.config import settings
        bind = db.get_bind()
        target = f"{bind.url.host}/{bind.url.database}" if bind is not None and bind.url else "unknown"
        return f"env={getattr(settings, 'ENVIRONMENT', 'unknown')} db={target}"
    except Exception:
        return "unknown"


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


def get_catalog_access(request: Request, db: Session = Depends(get_db), user=Depends(get_optional_catalog_user)):
    if user is None:
        return CatalogAccess("public")
    if not profile_status_is_active(user):
        raise HTTPException(403, "Inactive user")
    # TEMPORARY DIAGNOSTIC — capture the raw values exactly as they arrived
    # on the `user` dict (i.e. whatever survived token_auth.payload_to_user),
    # before any remapping below, so we can see whether e.g. a camelCase
    # tenantRole/userRole claim actually made it into user["tenant_role"] /
    # user["role"] at all.
    _diag_raw_user_id = user.get("id")
    _diag_raw_role = user.get("role")
    _diag_raw_tenant_role = user.get("tenant_role")
    _diag_raw_user_role = user.get("user_role")
    role = str(user.get("role") or "").lower()
    tenant_role = str(user.get("tenant_role") or "").lower()
    if role == "super_admin":
        return CatalogAccess(role)
    # Auth maps both tenant_admin and internal_user to provider. Distinguish
    # them here without altering the global role mapping used by other modules.
    if tenant_role == "internal_user" or role == "internal_user":
        role = "provider"
    elif tenant_role in ("tenant_owner", "tenant_admin"):
        role = "admin"
    if role == "customer":
        return CatalogAccess(role)
    if role not in ("admin", "provider"):
        raise HTTPException(403, "Catalog access denied")
    authorization = request.headers.get("authorization", "")
    token = authorization.split(" ", 1)[1] if authorization.lower().startswith("bearer ") else get_web_session_cookie_token(request)
    tenant = resolve_auth_tenant_id_with_db(db, user, access_token=token)
    try:
        tenant_id = UUID(str(tenant))
        provider_id = UUID(str(user.get("id"))) if role == "provider" else None
    except (ValueError, TypeError):
        raise HTTPException(403, "Authenticated tenant/user identity required")
    # TEMPORARY DIAGNOSTIC — remove once the GET /services empty-result
    # investigation is closed. No tokens/headers/PII: path, raw claim values
    # as received on the `user` dict, resolved CatalogAccess fields, and a
    # host/dbname-only db identifier.
    logger.info(
        "[DIAG get_catalog_access] path=%s | raw: user_id=%s role=%s tenant_role=%s user_role=%s | "
        "resolved: CatalogAccess.role=%s CatalogAccess.tenant_id=%s CatalogAccess.provider_user_id=%s | %s",
        request.url.path,
        _diag_raw_user_id, _diag_raw_role, _diag_raw_tenant_role, _diag_raw_user_role,
        role, tenant_id, provider_id, _safe_db_target(db),
    )
    return CatalogAccess(role, tenant_id, provider_id)


def require_catalog_writer(access: CatalogAccess = Depends(get_catalog_access)):
    if access.role == "public":
        raise HTTPException(401, "Not authenticated")
    if access.role not in ("admin", "super_admin"):
        raise HTTPException(403, "Providers are read-only; Enterprise Admin access required")
    return access


def scope_catalog_query(query, model, access: CatalogAccess | None):
    if access is None or access.role in ("public", "customer", "super_admin"):
        return query
    query = query.filter(
        model.enterprise.has(Enterprise.tenant_id == access.tenant_id),
        or_(model.tenant_id.is_(None), model.tenant_id == access.tenant_id),
    )
    if access.role == "provider":
        query = query.filter(model.provider_user_id == access.provider_user_id)
    return query


def validate_catalog_write(db, enterprise_id, supplied_tenant_id, access):
    if access is None:  # Internal callers retain their existing service contract.
        return
    require_catalog_writer(access)
    if access.role == "super_admin":
        return
    enterprise = db.query(Enterprise).filter(Enterprise.id == enterprise_id, Enterprise.is_deleted.is_(False)).first()
    if not enterprise or enterprise.tenant_id != access.tenant_id:
        raise HTTPException(403, "Not authorized for this tenant")
    if supplied_tenant_id is not None and supplied_tenant_id != access.tenant_id:
        raise HTTPException(403, "Not authorized for this tenant")
