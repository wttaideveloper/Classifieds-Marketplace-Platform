import hashlib
import logging
import time
from uuid import UUID

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)


def _extract_items(payload) -> list[dict] | None:
    """Rows of an Invigorate ``ListResource`` (``{"message", "data": [...], "total"}``).

    Also tolerates a bare list or ``items``/``members`` keys. ``None`` = unrecognised shape.
    """
    items = payload
    if isinstance(payload, dict):
        items = payload.get("data")
        if items is None:
            items = payload.get("items") if payload.get("items") is not None else payload.get("members")
        if isinstance(items, dict):
            items = items.get("items") or items.get("members")
    if not isinstance(items, list):
        return None
    return [item for item in items if isinstance(item, dict)]


def _get_items(url: str, headers: dict, *, params: dict | None = None, what: str) -> list[dict] | None:
    """GET a list endpoint. ``None`` on any failure; logs method/url/status only (never headers)."""
    try:
        kwargs: dict = {"headers": headers, "timeout": 15}
        if params:
            kwargs["params"] = params
        response = requests.get(url, **kwargs)
        response.raise_for_status()
        payload = response.json()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        logger.error("Invigorate %s lookup failed: GET %s -> HTTP %s", what, url, status)
        return None
    except Exception as exc:
        logger.error("Invigorate %s lookup failed: GET %s (%s)", what, url, type(exc).__name__)
        return None
    items = _extract_items(payload)
    if items is None:
        logger.error("Invigorate %s lookup returned an unexpected shape: GET %s", what, url)
    return items


def _internal_get(
    path: str, access_token: str | None, *, params: dict | None = None, what: str
) -> list[dict] | None:
    """Call a documented Identity-API ``/api/v1/internal/*`` endpoint.

    Credentials are whatever exist: ``X-Internal-Api-Key`` when configured (the documented
    requirement) and the caller's Keycloak Bearer token when one was supplied (a requirement
    of the documented ``/internal/*`` APIs). At least one credential is required — without
    either there is nothing to authenticate with and no request is made. Token-only lookups
    are legitimate for the member endpoints that take no tenant path.
    """
    base = settings.invigorate_admin_api_base_url
    key = settings.INVIGORATE_INTERNAL_API_KEY.strip()
    if not base:
        return None
    headers: dict = {}
    if key:
        headers["X-Internal-Api-Key"] = key
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if not headers:
        return None
    return _get_items(f"{base}{path}", headers, params=params, what=what)


def list_tenants(access_token: str | None = None) -> list[dict]:
    """Tenants via Identity API GET /api/v1/internal/tenants (``data[]``: id, slug, name, status, ...)."""
    return _internal_get("/api/v1/internal/tenants", access_token, what="tenants") or []


def list_super_admins(access_token: str | None = None) -> list[dict]:
    """Platform Super Admins via Identity API GET /api/v1/internal/super-admins.

    Documented ``data[]`` fields: ``id`` (application user id), ``keycloakId`` (identity only —
    never a recipient id), ``email``, ``isSuperAdmin``, ``status``, ``inviteStatus``.
    """
    return _internal_get("/api/v1/internal/super-admins", access_token, what="super admins") or []


_MEMBERSHIP_MARKERS = ("membershipId", "roleName", "tenantRbacRoles")


def member_application_user_id(record: dict) -> UUID | None:
    """Application user id of a tenant-member record.

    Documented member shapes carry the user id in ``userId``; their ``id`` /
    ``membershipId`` is the MEMBERSHIP record id and must never be used. ``id`` is
    only accepted for records with no membership fields (flat user records).
    ``keycloakId`` / ``keycloak_id`` are never accepted.
    """
    for field in ("application_user_id", "user_id", "userId"):
        raw = record.get(field)
        if raw:
            try:
                return UUID(str(raw))
            except (TypeError, ValueError):
                return None
    if any(marker in record for marker in _MEMBERSHIP_MARKERS):
        return None
    raw = record.get("id")
    if raw:
        try:
            return UUID(str(raw))
        except (TypeError, ValueError):
            return None
    return None


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
    """Members of ANY tenant via Identity API GET /api/v1/internal/tenants/{tenant_id}/users.

    Documented ``data[]`` fields: ``userId`` (application user id), ``membershipId``, ``role``
    (e.g. tenant_owner), ``membershipStatus``, ``userStatus``, ``isSuperAdmin``. Requires
    ``X-Internal-Api-Key`` plus the caller's Bearer token. Complete records are returned so
    callers keep their RBAC fields; use :func:`list_tenant_user_ids` for ids only.
    """
    return (
        _internal_get(
            f"/api/v1/internal/tenants/{tenant_id}/users",
            access_token,
            what="tenant users",
        )
        or []
    )


def list_tenant_members(access_token: str) -> list[dict] | None:
    """List the members of the caller's own tenant via Invigorate
    GET /api/v1/tenant/members, authenticated with the caller's Bearer token.

    The endpoint takes no tenant parameter: the tenant is implied by the token.
    Callers MUST confirm the token's tenant is the tenant they need (see
    ``fetch_tenant_me_profile``) before trusting the result.

    Returns ``None`` when the lookup failed (so callers can tell a failure from
    an empty tenant). The token is never logged.
    """
    base = settings.invigorate_admin_api_base_url
    if not access_token or not base:
        return None
    return _get_items(
        f"{base}/api/v1/tenant/members",
        {"Authorization": f"Bearer {access_token}"},
        what="tenant members",
    )


def list_tenant_user_ids(tenant_id: UUID, access_token: str | None = None) -> list[UUID]:
    """Resolve tenant member ids via Invigorate internal API when configured."""
    items = list_tenant_users(tenant_id, access_token)

    user_ids: list[UUID] = []
    for item in items:
        user_id = member_application_user_id(item)
        if user_id is not None:
            user_ids.append(user_id)
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
