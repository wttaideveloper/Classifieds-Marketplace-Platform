"""Resolve authenticated tenant / enterprise context from JWT / WebAuth session user dict."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.enterprise_model import Enterprise


def _first_id(*values) -> str | None:
    for value in values:
        if value is None or value == "":
            continue
        if isinstance(value, dict):
            nested = _first_id(
                value.get("tenant_id"),
                value.get("tenantId"),
                value.get("id"),
                value.get("organization_id"),
                value.get("organizationId"),
            )
            if nested:
                return nested
            continue
        return str(value)
    return None


def resolve_auth_tenant_id(current_user: dict | None) -> str | None:
    """Extract tenant_id from authenticated session/JWT claims — never from query params."""
    if not current_user:
        return None

    direct = _first_id(
        current_user.get("tenant_id"),
        current_user.get("tenantId"),
        current_user.get("tenant_id_claim"),
        current_user.get("org_id"),
        current_user.get("organization_id"),
        current_user.get("organizationId"),
    )
    if direct:
        return direct

    membership = current_user.get("membership")
    if isinstance(membership, dict):
        nested = _first_id(
            membership.get("tenant_id"),
            membership.get("tenantId"),
            membership.get("organization_id"),
            membership.get("organizationId"),
            membership.get("tenant"),
            membership.get("organization"),
            membership.get("id"),
        )
        if nested:
            return nested

    tenants = current_user.get("tenants")
    if isinstance(tenants, list) and tenants:
        first = tenants[0]
        if isinstance(first, dict):
            nested = _first_id(
                first.get("tenant_id"),
                first.get("tenantId"),
                first.get("id"),
                first.get("organization_id"),
                first.get("organizationId"),
            )
            if nested:
                return nested
        elif first:
            return str(first)

    return None


def resolve_auth_enterprise_id(current_user: dict | None) -> str | None:
    if not current_user:
        return None
    value = current_user.get("enterprise_id") or current_user.get("enterpriseId")
    return str(value) if value else None


def resolve_auth_tenant_id_with_db(db: Session, current_user: dict | None) -> str | None:
    """Resolve tenant from WebAuth/JWT claims, then Enterprise lookup fallbacks."""
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

    # WebAuth sessions sometimes carry tenant_slug without a UUID claim
    tenant_slug = current_user.get("tenant_slug") if current_user else None
    if tenant_slug and hasattr(Enterprise, "slug"):
        try:
            ent = (
                db.query(Enterprise)
                .filter(Enterprise.slug == tenant_slug, Enterprise.is_deleted.is_(False))
                .first()
            )
            if ent and ent.tenant_id:
                return str(ent.tenant_id)
        except Exception:
            pass

    return None
