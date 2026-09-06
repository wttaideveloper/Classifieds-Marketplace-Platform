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
            status_code=400,
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
