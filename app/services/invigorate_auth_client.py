import logging
from uuid import UUID

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)


def list_tenants() -> list[dict]:
    """Fetch all tenants from the Invigorate internal API."""
    if not settings.invigorate_internal_api_configured:
        return []

    url = f"{settings.INVIGORATE_AUTH_BASE_URL.rstrip('/')}/api/v1/internal/tenants"
    headers = {"X-Internal-Api-Key": settings.INVIGORATE_INTERNAL_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        logger.exception("Failed to fetch tenants from Invigorate internal API")
        return []

    items = payload.get("items") or payload.get("data") or payload
    return items if isinstance(items, list) else []


def _normalize_tenant_slug(value: str) -> str:
    return str(value).strip().lower()


def resolve_tenant_ids_from_slugs(slugs: list[str]) -> list[UUID]:
    """Resolve tenant slugs to canonical tenant UUIDs using the Invigorate tenant catalog."""
    from fastapi import HTTPException

    if not slugs:
        return []

    if not settings.invigorate_internal_api_configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "Tenant slug resolution requires INVIGORATE_AUTH_BASE_URL and "
                "INVIGORATE_INTERNAL_API_KEY to be configured"
            ),
        )

    tenants = list_tenants()
    if not tenants:
        raise HTTPException(
            status_code=503,
            detail="Tenant catalog is unavailable — cannot resolve tenant slugs",
        )

    by_slug: dict[str, UUID] = {}
    for tenant in tenants:
        if not isinstance(tenant, dict):
            continue
        slug = tenant.get("slug")
        tenant_id = tenant.get("id")
        if not slug or not tenant_id:
            continue
        try:
            by_slug[_normalize_tenant_slug(slug)] = UUID(str(tenant_id))
        except ValueError:
            continue

    resolved: list[UUID] = []
    unknown: list[str] = []
    for raw_slug in slugs:
        key = _normalize_tenant_slug(raw_slug)
        tenant_uuid = by_slug.get(key)
        if tenant_uuid is None:
            unknown.append(raw_slug)
        else:
            resolved.append(tenant_uuid)

    if unknown:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Unknown tenant slug(s)",
                "unknown_slugs": unknown,
            },
        )

    return resolved


def list_tenant_user_ids(tenant_id: UUID) -> list[UUID]:
    """Resolve tenant members via Invigorate internal API when configured."""
    if not settings.invigorate_internal_api_configured:
        return []

    url = (
        f"{settings.INVIGORATE_AUTH_BASE_URL.rstrip('/')}"
        f"/api/v1/internal/tenants/{tenant_id}/users"
    )
    headers = {"X-Internal-Api-Key": settings.INVIGORATE_INTERNAL_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        logger.exception("Failed to fetch tenant users from Invigorate internal API")
        return []

    items = payload.get("items") or payload.get("data") or payload
    if not isinstance(items, list):
        return []

    user_ids: list[UUID] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_id = (
            item.get("user_id")
            or item.get("id")
            or item.get("keycloak_id")
            or (item.get("user") or {}).get("id")
        )
        if raw_id:
            try:
                user_ids.append(UUID(str(raw_id)))
            except ValueError:
                continue
    return user_ids


def fetch_tenant_me_profile(access_token: str) -> dict | None:
    """Resolve the authenticated user's canonical tenant via Invigorate's
    GET /api/v1/tenant/me — the same lookup the Web frontend's /tenant/me
    call performs. Unlike /auth/me (a user profile with no direct tenant_id
    field), this returns {"data": {"id": "<tenant_id>", "slug": ..., ...}} —
    `id` here IS the tenant id, not a user id."""
    if not access_token or not settings.INVIGORATE_AUTH_BASE_URL.strip():
        return None

    url = f"{settings.INVIGORATE_AUTH_BASE_URL.rstrip('/')}/api/v1/tenant/me"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
    except Exception:
        logger.exception("Failed to fetch Invigorate /tenant/me profile")
        return None

    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else None


def fetch_auth_me_profile(access_token: str) -> dict | None:
    """Resolve the authenticated Invigorate user profile (isSuperAdmin, status, …)."""
    if not access_token or not settings.INVIGORATE_AUTH_BASE_URL.strip():
        return None

    url = f"{settings.INVIGORATE_AUTH_BASE_URL.rstrip('/')}/api/v1/auth/me"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
    except Exception:
        logger.exception("Failed to fetch Invigorate /auth/me profile")
        return None

    return payload if isinstance(payload, dict) else None


def _extract_application_user_id(profile: dict | None) -> str | None:
    """Resolve the canonical application (Postgres) user id from an
    Invigorate /auth/me response. The response is sometimes returned flat
    and sometimes nested under data/user/profile (same shape variance
    fetch_tenant_me_profile / _unwrap_auth_me_profile already handle for
    other fields) — check both, and both `id` and common alias field names."""
    if not isinstance(profile, dict):
        return None
    candidates = [profile]
    for key in ("data", "user", "profile"):
        nested = profile.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    for candidate in candidates:
        for field in ("id", "userId", "user_id"):
            value = candidate.get(field)
            if value:
                return str(value)
    return None


def fetch_application_user_id(access_token: str) -> str | None:
    """Resolve the caller's canonical application user id via Invigorate
    GET /api/v1/auth/me, forwarding the SAME bearer token from the incoming
    request. Auth team confirmed contract: JWT `sub` is the Keycloak
    identity only, never the application/Postgres user id — this call is
    the sole source of truth for the application user id used by domain
    queries (Service.provider_user_id, conversation provider ids, etc.)."""
    profile = fetch_auth_me_profile(access_token)
    return _extract_application_user_id(profile)


def fetch_internal_user_by_id(user_id: str) -> dict | None:
    """Look up an Invigorate internal user record by id / Keycloak sub."""
    if not user_id or not settings.invigorate_internal_api_configured:
        return None

    base = settings.INVIGORATE_AUTH_BASE_URL.rstrip("/")
    headers = {"X-Internal-Api-Key": settings.INVIGORATE_INTERNAL_API_KEY}
    # Prefer explicit user id path; fall back to users?id= if the first 404s.
    candidates = (
        f"{base}/api/v1/internal/users/{user_id}",
        f"{base}/api/v1/internal/users?id={user_id}",
    )
    for url in candidates:
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            payload = response.json()
        except Exception:
            logger.exception("Failed to fetch Invigorate internal user %s from %s", user_id, url)
            continue

        if isinstance(payload, dict):
            items = payload.get("items") or payload.get("data")
            if isinstance(items, list) and items and isinstance(items[0], dict):
                return items[0]
            return payload
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return payload[0]
    return None
