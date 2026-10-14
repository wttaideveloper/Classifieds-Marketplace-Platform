"""Who may moderate a review: a Super Admin, for every business. Enterprise Admins only look."""
from __future__ import annotations

from fastapi import HTTPException


MODERATE_DENIED = "Only a Super Admin can moderate reviews"


def _norm(value) -> str | None:
    return str(value).lower() if value else None


def assert_can_moderate(access, *owner_tenant_ids) -> None:
    """403 unless `access` (a CatalogAccess) is a Super Admin. The owner tenants are not needed any more (a Super Admin
    moderates every business) and are kept in the signature so the callers stay the same."""
    if access is None or access.role == "public":
        raise HTTPException(status_code=401, detail="Not authenticated")
    if access.role == "super_admin":
        return
    # Enterprise Admins and providers can look at reviews but not change them.
    raise HTTPException(status_code=403, detail=MODERATE_DENIED)


def assert_super_admin_actor(actor: dict | None) -> None:
    """403 unless `actor` (the signed-in user) is an active Platform Super Admin."""
    from app.services.super_admin_identity import profile_status_is_active

    if not actor or str(actor.get("role") or "").lower() != "super_admin" or not profile_status_is_active(actor):
        raise HTTPException(status_code=403, detail=MODERATE_DENIED)


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
