import logging
from uuid import UUID

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)


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
