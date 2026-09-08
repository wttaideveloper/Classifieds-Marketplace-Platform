"""Platform Super Admin identity resolution for approval / audit endpoints."""

from __future__ import annotations

import logging
from typing import Any

from app.services.invigorate_auth_client import (
    fetch_auth_me_profile,
    fetch_internal_user_by_id,
)

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = frozenset({"active", "enabled", "ok", "approved"})


def _truthy_flag(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, (int, float)) and value == 1:
        return True
    if isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"}:
        return True
    return False


def _unwrap_profile(payload: dict | None) -> dict | None:
    if not isinstance(payload, dict):
        return None
    for key in ("data", "user", "profile"):
        nested = payload.get(key)
        if isinstance(nested, dict) and (
            "isSuperAdmin" in nested
            or "is_super_admin" in nested
            or "status" in nested
            or "id" in nested
            or "email" in nested
        ):
            return nested
    return payload


def profile_is_super_admin(profile: dict | None) -> bool:
    """True when profile/JWT claims mark a dedicated Platform Super Admin."""
    if not isinstance(profile, dict):
        return False
    if profile.get("role") == "super_admin":
        return True
    if _truthy_flag(profile.get("isSuperAdmin")) or _truthy_flag(profile.get("is_super_admin")):
        return True
    nested = profile.get("user")
    if isinstance(nested, dict):
        if nested.get("role") == "super_admin":
            return True
        if _truthy_flag(nested.get("isSuperAdmin")) or _truthy_flag(nested.get("is_super_admin")):
            return True
    return False


def profile_status_is_active(profile: dict | None) -> bool:
    """Absent status is treated as active (JWT-only path). Explicit non-active fails."""
    if not isinstance(profile, dict):
        return True
    status = profile.get("status")
    if status is None and isinstance(profile.get("user"), dict):
        status = profile["user"].get("status")
    if status is None:
        return True
    return str(status).strip().lower() in _ACTIVE_STATUSES


def resolve_platform_super_admin_user(
    current_user: dict,
    *,
    access_token: str | None = None,
) -> dict | None:
    """Return enriched user dict when this identity is an active Platform Super Admin.

    Resolution order:
    1. Claims already on ``current_user`` (from Keycloak JWT via get_current_user)
    2. Invigorate ``GET /api/v1/auth/me`` with the same Bearer token
    3. Invigorate internal user lookup by ``sub`` / user id
    """
    candidates: list[dict] = [current_user]

    if access_token:
        me = _unwrap_profile(fetch_auth_me_profile(access_token))
        if me:
            candidates.append(me)

    user_id = current_user.get("id")
    if user_id:
        internal = _unwrap_profile(fetch_internal_user_by_id(str(user_id)))
        if internal:
            candidates.append(internal)

    for profile in candidates:
        if profile_is_super_admin(profile) and profile_status_is_active(profile):
            enriched = {
                **current_user,
                "role": "super_admin",
                "isSuperAdmin": True,
            }
            status = profile.get("status")
            if status is None and isinstance(profile.get("user"), dict):
                status = profile["user"].get("status")
            if status is not None:
                enriched["status"] = status
            else:
                enriched.setdefault("status", "active")
            return enriched

    return None
