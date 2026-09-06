"""Resolve authenticated tenant / enterprise context from JWT user dict."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.enterprise_model import Enterprise


def resolve_auth_tenant_id(current_user: dict | None) -> str | None:
    """Extract tenant_id from authenticated user claims — never from query params."""
    if not current_user:
        return None

    for key in ("tenant_id", "tenantId", "tenant_id_claim", "org_id", "organization_id"):
        value = current_user.get(key)
        if value:
            return str(value)

    membership = current_user.get("membership")
    if isinstance(membership, dict):
        for key in ("tenant_id", "tenantId", "id"):
            value = membership.get(key)
            if value:
                return str(value)

    tenants = current_user.get("tenants")
    if isinstance(tenants, list) and tenants:
        first = tenants[0]
        if isinstance(first, dict):
            for key in ("tenant_id", "tenantId", "id"):
                value = first.get(key)
                if value:
                    return str(value)
        elif first:
            return str(first)

    return None


def resolve_auth_enterprise_id(current_user: dict | None) -> str | None:
    if not current_user:
        return None
    value = current_user.get("enterprise_id") or current_user.get("enterpriseId")
    return str(value) if value else None


def resolve_auth_tenant_id_with_db(db: Session, current_user: dict | None) -> str | None:
    """Resolve tenant from JWT claims, then Enterprise lookup fallbacks."""
    tenant_id = resolve_auth_tenant_id(current_user)
    if tenant_id:
        return tenant_id

    enterprise_id = resolve_auth_enterprise_id(current_user)
    if enterprise_id:
        try:
            ent = (
                db.query(Enterprise)
                .filter(Enterprise.id == UUID(str(enterprise_id)), Enterprise.is_deleted.is_(False))
                .first()
            )
            if ent and ent.tenant_id:
                return str(ent.tenant_id)
        except (ValueError, TypeError):
            pass

    return None
