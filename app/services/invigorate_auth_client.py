import hashlib
import logging
import time
from uuid import UUID

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)


def _identity_headers(access_token: str | None) -> dict | None:
    """Credentials for an identity lookup: the internal API key when configured, and the caller's own
    Bearer token when one is supplied (so a lookup still works where only the token is available).
    None = nothing to authenticate with, so no request should be made."""
    if not settings.INVIGORATE_AUTH_BASE_URL.strip():
        return None
    headers: dict = {}
    if settings.INVIGORATE_INTERNAL_API_KEY.strip():
        headers["X-Internal-Api-Key"] = settings.INVIGORATE_INTERNAL_API_KEY
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers or None


def list_tenants(access_token: str | None = None) -> list[dict]:
    """Fetch all tenants from the Invigorate API (internal key and/or the caller's Bearer token)."""
    headers = _identity_headers(access_token)
    if headers is None:
        return []

    url = f"{settings.INVIGORATE_AUTH_BASE_URL.rstrip('/')}/api/v1/internal/tenants"
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


def list_tenant_users(tenant_id: UUID, access_token: str | None = None) -> list[dict]:
    """Resolve tenant member records via the established internal API.

    Keep the complete records here rather than losing their RBAC fields at the
    boundary.  Callers that only need ids should continue to use
    :func:`list_tenant_user_ids` below.
    """
    headers = _identity_headers(access_token)
    if headers is None:
        return []

    url = (
        f"{settings.INVIGORATE_AUTH_BASE_URL.rstrip('/')}"
        f"/api/v1/internal/tenants/{tenant_id}/users"
    )
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

    return [item for item in items if isinstance(item, dict)]


def list_tenant_user_ids(tenant_id: UUID, access_token: str | None = None) -> list[UUID]:
    """Resolve tenant member ids via the Invigorate API (internal key and/or the caller's token)."""
    items = list_tenant_users(tenant_id, access_token) if access_token else list_tenant_users(tenant_id)

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


# --- application roles from the /auth/me profile ------------------------------------------------
# The access token's own claims are the first source of a user's role. A user who has not joined a
# tenant often has none (the role then resolved to null and Products/Services answered 403), while the
# same user's /auth/me profile says roles.tenantRole = "external_user", roles.userRole = "customer".
# The profile is the Auth team's source of truth, so it is the fallback.

_ROLES_CACHE: dict[str, tuple[float, dict]] = {}
_ROLES_TTL_SECONDS = 60.0
_ROLES_CACHE_MAX = 2048


def _extract_profile_roles(profile: dict | None) -> dict | None:
    """{"tenant_role", "user_role", "tenant_rbac_roles"} from a /auth/me response, or None."""
    if not isinstance(profile, dict):
        return None
    candidates = [profile] + [profile[k] for k in ("data", "user", "profile") if isinstance(profile.get(k), dict)]
    for candidate in candidates:
        roles = candidate.get("roles")
        if not isinstance(roles, dict):
            continue
        found = {
            "tenant_role": roles.get("tenantRole") or roles.get("tenant_role"),
            "user_role": roles.get("userRole") or roles.get("user_role"),
            "tenant_rbac_roles": roles.get("tenantRbacRoles") or roles.get("tenant_rbac_roles"),
        }
        found = {k: v for k, v in found.items() if v}
        if found:
            return found
    return None


def _cached_auth_me_profile(access_token: str) -> dict | None:
    """The caller's /auth/me profile, cached a minute per token (failures are never cached)."""
    key = hashlib.sha256(access_token.encode()).hexdigest()
    now = time.monotonic()
    hit = _ROLES_CACHE.get(key)
    if hit and hit[0] > now:
        return hit[1]
    profile = fetch_auth_me_profile(access_token)
    if isinstance(profile, dict):
        if len(_ROLES_CACHE) >= _ROLES_CACHE_MAX:
            _ROLES_CACHE.pop(min(_ROLES_CACHE, key=lambda k: _ROLES_CACHE[k][0]), None)
        _ROLES_CACHE[key] = (now + _ROLES_TTL_SECONDS, profile)
    return profile


def fetch_application_roles(access_token: str) -> dict | None:
    """Roles the Auth service reports for the caller (a request that needs the fallback does not add a
    network round trip every time)."""
    if not access_token:
        return None
    return _extract_profile_roles(_cached_auth_me_profile(access_token))


def fetch_profile_email(access_token: str | None) -> tuple[str | None, bool]:
    """(email, verified) from the caller's /auth/me profile. The token's own email claim can be missing or
    differ in case/spelling from what the user typed, the profile is the Auth service's record of it.
    `verified` is False only when the profile explicitly says emailVerified is false."""
    if not access_token:
        return None, False
    profile = _cached_auth_me_profile(access_token)
    if not isinstance(profile, dict):
        return None, False
    for candidate in [profile] + [profile[k] for k in ("data", "user", "profile") if isinstance(profile.get(k), dict)]:
        email = candidate.get("email")
        if isinstance(email, str) and email.strip():
            return email.strip().lower(), candidate.get("emailVerified", True) is not False
    return None, False
