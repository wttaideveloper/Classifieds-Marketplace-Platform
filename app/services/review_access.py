"""Who may moderate a review: the Enterprise Admin of the business that owns the item, or a Super Admin."""
from __future__ import annotations

from fastapi import HTTPException


def _norm(value) -> str | None:
    return str(value).lower() if value else None


def assert_can_moderate(access, *owner_tenant_ids) -> None:
    """403 unless `access` (a CatalogAccess) may act on an item owned by these tenants.

    Every owner tenant that is set must be the caller's tenant (an item has its enterprise's tenant and
    sometimes its own copy of it). At least one must be set: an item with no known owner fails closed.
    """
    if access is None or access.role == "public":
        raise HTTPException(status_code=401, detail="Not authenticated")
    if access.role == "super_admin":
        return
    if access.role != "admin":
        raise HTTPException(status_code=403, detail="Providers are read-only; Enterprise Admin access required")
    owners = [_norm(t) for t in owner_tenant_ids if t]
    caller = _norm(access.tenant_id)
    if not caller or not owners or any(owner != caller for owner in owners):
        raise HTTPException(status_code=403, detail="Not authorized for this tenant")


def can_view_all_tenants(access) -> bool:
    return access is not None and access.role == "super_admin"


def assert_staff_in_tenant(db, current_user: dict, access_token, *owner_tenant_ids) -> None:
    """For a staff caller (admin / provider / super_admin) acting on an item: 403 unless the item belongs to the
    caller's own business. An active Super Admin may act on any item."""
    from app.core.auth_context import resolve_auth_tenant_id_with_db
    from app.services.super_admin_identity import profile_status_is_active

    role = str((current_user or {}).get("role") or "").lower()
    if role == "super_admin" and profile_status_is_active(current_user):
        return
    owners = [_norm(t) for t in owner_tenant_ids if t]
    caller = _norm(resolve_auth_tenant_id_with_db(db, current_user, access_token=access_token))
    if not caller or not owners or any(owner != caller for owner in owners):
        raise HTTPException(status_code=403, detail="Not authorized for this tenant")
