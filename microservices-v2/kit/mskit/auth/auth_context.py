"""Resolve authenticated tenant / enterprise context from JWT / WebAuth session user dict."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from mskit.auth.tenant_lookup import tenant_id_for_enterprise


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


def _unwrap_auth_me_profile(payload: dict | None) -> dict | None:
    if not isinstance(payload, dict):
        return None
    for key in ("data", "user", "profile"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            return nested
    return None


def resolve_auth_tenant_id_with_db(
    db: Session,
    current_user: dict | None,
    *,
    access_token: str | None = None,
) -> str | None:
    """Resolve tenant from WebAuth/JWT claims, then Enterprise lookup fallbacks.

    Enterprise Admin WebAuth session tokens frequently carry no tenant claim at
    all (Keycloak issues them without a tenant_id/membership claim for this
    login flow). When ``access_token`` is supplied and local claim resolution
    comes up empty, fall back to a live Invigorate ``GET /api/v1/tenant/me``
    call with that same session token/cookie — the exact same lookup the Web
    frontend's own ``/tenant/me`` call performs, and the only one confirmed to
    return the canonical tenant id (``data.id``). ``GET /api/v1/auth/me`` is
    a user-profile endpoint with no direct tenant_id field and is kept only
    as a secondary fallback in case a future response shape adds one.
    """
    tenant_id = resolve_auth_tenant_id(current_user)
    if tenant_id:
        return tenant_id

    enterprise_id = resolve_auth_enterprise_id(current_user)
    if enterprise_id:
        # Monolith: SELECT tenant_id FROM enterprises WHERE id = :enterprise_id AND NOT is_deleted.
        # Enterprises belong to the catalog service: answered by its database there, by its
        # internal API everywhere else (see mskit/auth/tenant_lookup.py).
        tenant = tenant_id_for_enterprise(db, enterprise_id)
        if tenant:
            return tenant
    # (The monolith also tried an Enterprise.slug lookup guarded by hasattr(Enterprise, "slug");
    # the model has no slug column, so that branch never ran and is not carried over.)

    if access_token:
        from mskit.auth.invigorate_auth_client import fetch_auth_me_profile, fetch_tenant_me_profile

        tenant_profile = fetch_tenant_me_profile(access_token)
        if isinstance(tenant_profile, dict) and tenant_profile.get("id"):
            return str(tenant_profile["id"])

        # Secondary fallback — kept in case /auth/me's response shape ever
        # gains a direct tenant claim of its own.
        profile = fetch_auth_me_profile(access_token)
        for candidate in (profile, _unwrap_auth_me_profile(profile)):
            resolved = resolve_auth_tenant_id(candidate)
            if resolved:
                return resolved

    return None
