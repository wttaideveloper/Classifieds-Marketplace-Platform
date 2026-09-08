import logging
import time

import requests
from fastapi import HTTPException, status
from jose import ExpiredSignatureError, JWTError, jwk, jwt

from app.core.config import settings

logger = logging.getLogger(__name__)

_JWKS_CACHE: dict = {"keys": None, "fetched_at": 0.0}
_JWKS_TTL_SECONDS = 300


def _fetch_jwks() -> dict:
    now = time.time()
    if _JWKS_CACHE["keys"] is not None and now - _JWKS_CACHE["fetched_at"] < _JWKS_TTL_SECONDS:
        return _JWKS_CACHE["keys"]

    url = settings.keycloak_jwks_url
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    _JWKS_CACHE["keys"] = response.json()
    _JWKS_CACHE["fetched_at"] = now
    return _JWKS_CACHE["keys"]


def _decode_local_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[settings.ALGORITHM],
    )


def _normalize_audience_list(aud) -> list[str]:
    if aud is None:
        return []
    if isinstance(aud, str):
        return [aud]
    if isinstance(aud, (list, tuple)):
        return [str(item) for item in aud]
    return [str(aud)]


def _validate_keycloak_audience(payload: dict) -> None:
    """Accept tokens where aud or azp matches KEYCLOAK_AUDIENCE.

    Keycloak access tokens often use aud=[account, broker, realm-management]
    while azp identifies the OAuth client (e.g. invigorate-api).
    """
    expected = settings.KEYCLOAK_AUDIENCE.strip()
    if not expected:
        return

    audiences = _normalize_audience_list(payload.get("aud"))
    azp = payload.get("azp")
    if expected in audiences or azp == expected:
        return

    raise JWTError(
        f"Token audience mismatch: expected aud or azp '{expected}', got aud={audiences} azp={azp!r}"
    )


def _decode_keycloak_token(token: str) -> dict:
    if not settings.keycloak_configured:
        raise JWTError("Keycloak is not configured")

    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    jwks = _fetch_jwks()
    key_data = next((item for item in jwks.get("keys", []) if item.get("kid") == kid), None)
    if key_data is None:
        raise JWTError("Unable to find matching Keycloak signing key")

    public_key = jwk.construct(key_data)
    payload = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        issuer=settings.KEYCLOAK_ISSUER,
        options={"verify_aud": False},
    )
    _validate_keycloak_audience(payload)
    return payload


def _normalize_slug(value: str | None) -> str | None:
    if not value:
        return None
    return str(value).strip().lower().replace("-", "_")


def _is_super_admin_claim(payload: dict) -> bool:
    """Dedicated Platform Super Admin login sets isSuperAdmin on the Keycloak access token."""
    for key in ("isSuperAdmin", "is_super_admin"):
        value = payload.get(key)
        if value is True:
            return True
        if isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"}:
            return True

    nested_user = payload.get("user")
    if isinstance(nested_user, dict):
        for key in ("isSuperAdmin", "is_super_admin"):
            value = nested_user.get(key)
            if value is True:
                return True
            if isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"}:
                return True

    # Some Keycloak clients put the flag under realm / client roles
    roles: list[str] = []
    realm_access = payload.get("realm_access") or {}
    roles.extend(realm_access.get("roles") or [])
    for client_roles in (payload.get("resource_access") or {}).values():
        if isinstance(client_roles, dict):
            roles.extend(client_roles.get("roles") or [])
    normalized = {_normalize_slug(role) for role in roles if _normalize_slug(role)}
    if normalized & {"super_admin", "superadmin", "platform_super_admin"}:
        return True
    return False


def _map_keycloak_role(payload: dict) -> str | None:
    if _is_super_admin_claim(payload):
        return "super_admin"

    explicit_role = _normalize_slug(payload.get("role"))
    if explicit_role in {"admin", "super_admin", "provider", "customer"}:
        return explicit_role

    tenant_role = _normalize_slug(payload.get("tenant_role"))
    user_role = _normalize_slug(payload.get("user_role"))
    rbac_roles = {
        _normalize_slug(role)
        for role in (payload.get("tenant_rbac_roles") or [])
        if _normalize_slug(role)
    }

    if tenant_role == "external_user":
        return "customer"
    if tenant_role == "tenant_owner":
        return "admin"
    if tenant_role in {"tenant_admin", "internal_user"}:
        return "provider"

    if user_role in {"admin", "super_admin", "provider", "customer", "patient", "external_user"}:
        if user_role in {"patient", "external_user"}:
            return "customer"
        if user_role == "admin":
            return "admin"
        if user_role == "super_admin":
            return "super_admin"
        return user_role

    if rbac_roles & {"tenant_owner"}:
        return "admin"
    if rbac_roles & {"tenant_admin", "contributor", "reviewer", "publisher"}:
        return "provider"

    roles: list[str] = []
    realm_access = payload.get("realm_access") or {}
    roles.extend(realm_access.get("roles") or [])
    for client_roles in (payload.get("resource_access") or {}).values():
        roles.extend(client_roles.get("roles") or [])

    normalized = {_normalize_slug(role) for role in roles if _normalize_slug(role)}
    if "super_admin" in normalized:
        return "super_admin"
    if normalized & {"admin", "tenant_owner"}:
        return "admin"
    if normalized & {"provider", "tenant_admin", "internal_user", "staff"}:
        return "provider"
    if normalized & {"customer", "user", "patient", "external_user"}:
        return "customer"
    return next(iter(normalized), None)


def payload_to_user(payload: dict) -> dict:
    user_id = payload.get("id") or payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    # Dedicated Platform Super Admin tokens set isSuperAdmin=true and may also carry
    # unrelated role/tenant_role claims — always prefer the Super Admin flag.
    if _is_super_admin_claim(payload):
        role = "super_admin"
    else:
        explicit = _normalize_slug(payload.get("role"))
        role = explicit if explicit in {"admin", "super_admin", "provider", "customer"} else None
        role = role or _map_keycloak_role(payload)
    user = {
        "id": str(user_id),
        "role": role,
        "email": payload.get("email") or payload.get("preferred_username"),
    }

    tenant_role = payload.get("tenant_role")
    if tenant_role is not None:
        user["tenant_role"] = tenant_role
    user_role = payload.get("user_role")
    if user_role is not None:
        user["user_role"] = user_role
    rbac_roles = payload.get("tenant_rbac_roles")
    if rbac_roles is not None:
        user["tenant_rbac_roles"] = rbac_roles

    tenant_id = (
        payload.get("tenant_id")
        or payload.get("tenantId")
        or payload.get("org_id")
        or payload.get("organization_id")
        or payload.get("organizationId")
    )
    if not tenant_id:
        membership = payload.get("membership")
        if isinstance(membership, dict):
            tenant_id = (
                membership.get("tenant_id")
                or membership.get("tenantId")
                or membership.get("organization_id")
                or membership.get("organizationId")
            )
            if not tenant_id:
                nested = membership.get("tenant") or membership.get("organization")
                if isinstance(nested, dict):
                    tenant_id = nested.get("id") or nested.get("tenant_id") or nested.get("tenantId")
            if not tenant_id:
                tenant_id = membership.get("id")
    if tenant_id is not None:
        user["tenant_id"] = str(tenant_id)

    enterprise_id = payload.get("enterprise_id") or payload.get("enterpriseId")
    if enterprise_id is not None:
        user["enterprise_id"] = str(enterprise_id)

    for passthrough in (
        "membership",
        "tenants",
        "tenant_slug",
        "tenant_id_claim",
        "isSuperAdmin",
        "is_super_admin",
        "status",
        "user",
    ):
        if payload.get(passthrough) is not None:
            user[passthrough] = payload[passthrough]

    return user


def decode_access_token(token: str) -> dict:
    """Decode marketplace (HS256) or Keycloak (RS256) access tokens."""
    header = jwt.get_unverified_header(token)
    algorithm = header.get("alg", settings.ALGORITHM)

    # When Keycloak is configured, only RS256 is allowed (prevent alg confusion)
    if settings.keycloak_configured:
        if algorithm != "RS256":
            raise JWTError(f"Invalid algorithm '{algorithm}' — expected RS256 when Keycloak is configured")
        return _decode_keycloak_token(token)

    if algorithm == "HS256":
        return _decode_local_token(token)

    if algorithm == "RS256":
        return _decode_keycloak_token(token)

    return _decode_local_token(token)


def resolve_user_from_token(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        payload = decode_access_token(token)
        return payload_to_user(payload)
    except ExpiredSignatureError:
        return None
    except JWTError:
        return None
    except HTTPException:
        return None


def resolve_user_from_token_or_raise(token: str) -> dict:
    try:
        payload = decode_access_token(token)
        return payload_to_user(payload)
    except ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token expired") from exc
    except JWTError as exc:
        detail = "Invalid token"
        if settings.keycloak_configured:
            detail = (
                "Invalid token. Verify issuer, signature, expiry, and that aud or azp "
                f"matches KEYCLOAK_AUDIENCE ({settings.KEYCLOAK_AUDIENCE or 'not set'})."
            )
        raise HTTPException(status_code=401, detail=detail) from exc


def resolve_chat_user_from_token_or_raise(token: str) -> dict:
    """Resolve a regular access token or a correctly scoped chat token."""
    try:
        payload = decode_access_token(token)
        if payload.get("token_use") == "chat":
            scopes = payload.get("scope") or []
            if isinstance(scopes, str):
                scopes = scopes.split()
            if "chat" not in scopes:
                raise HTTPException(status_code=403, detail="Chat token scope is invalid")
        return payload_to_user(payload)
    except HTTPException:
        raise
    except ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token expired") from exc
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc


def is_chat_scoped_token(token: str) -> bool:
    """Return true only for locally issued tokens explicitly marked for chat."""
    try:
        return decode_access_token(token).get("token_use") == "chat"
    except (ExpiredSignatureError, JWTError, HTTPException):
        return False
