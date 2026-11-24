"""Notification recipient diagnostics (copied from app/services/notification_diagnostics.py and
app/repository/event_repo.py: is_platform_super_admin). Identity-provider lookups only.

v2: ``diagnose_enterprise_admins`` gets the record from its owner (a RemoteRecord whose ``enterprise`` carries the
owning tenant), so it no longer reads the enterprises table itself."""
from collections import Counter
from uuid import UUID

from mskit.auth import recipients as resolvers
from mskit.auth.super_admin_identity import profile_is_super_admin, profile_status_is_active
from mskit.settings import settings

ENTERPRISE_ADMIN_ROLES = ("admin", "tenant_owner")


def is_platform_super_admin(current_user: dict | None) -> bool:
    """True only for an active Platform Super Admin identity (claim-based, no network)."""
    return bool(current_user) and current_user.get("role") == "super_admin" and profile_status_is_active(current_user)


def _owner_tenant(entity):
    # event_owner_tenant_id: the record's own tenant, else its enterprise's
    value = getattr(entity, "tenant_id", None) or getattr(getattr(entity, "enterprise", None), "tenant_id", None)
    return str(value) if value else None


def diagnose_platform_admins(access_token: str | None = None) -> dict:
    """What ``resolve_platform_admin_user_ids`` sees (Identity ``GET /internal/super-admins``)."""
    configured = settings.invigorate_internal_api_configured
    records = resolvers.list_super_admins(access_token) if configured else []
    excluded: Counter = Counter()
    resolved: set[UUID] = set()
    for record in records:
        user_id = resolvers.member_application_user_id(record)
        if user_id is None:
            excluded["no_usable_user_id"] += 1
        elif not profile_is_super_admin(record):
            excluded["not_flagged_super_admin"] += 1
        elif not resolvers._active(record):
            excluded["super_admin_not_active"] += 1
        else:
            resolved.add(user_id)
    return {
        "source": "internal/super-admins",
        "bearer_token_supplied": bool(access_token),
        "super_admins_listed": len(records),
        "resolved_user_ids": sorted(str(u) for u in resolved),
        "excluded": dict(excluded),
    }


def diagnose_enterprise_admins(entity, access_token: str | None = None) -> dict:
    enterprise = getattr(entity, "enterprise", None)
    tenant_raw = getattr(enterprise, "tenant_id", None) or _owner_tenant(entity)
    try:
        tenant_id = UUID(str(tenant_raw)) if tenant_raw else None
    except (TypeError, ValueError):
        tenant_id = None
    result = {
        "tenant_id": str(tenant_id) if tenant_id else None,
        "tenant_source": "enterprise.tenant_id" if getattr(enterprise, "tenant_id", None) else "entity.tenant_id",
        "matching_roles": list(ENTERPRISE_ADMIN_ROLES),
        "users_scanned": 0,
        "roles_seen": {},
        "resolved_user_ids": [],
        "excluded": {},
    }
    if tenant_id is None or not settings.invigorate_internal_api_configured:
        return result
    users, source = resolvers.owning_tenant_users(tenant_id, access_token)
    result["source"] = source
    excluded: Counter = Counter()
    roles_seen: Counter = Counter()
    resolved: set[UUID] = set()
    for user in users:
        user_id = resolvers._application_user_id(user)
        roles = resolvers._roles(user)
        roles_seen.update(roles or {"(no role field)"})
        if user_id is None:
            excluded["no_usable_user_id"] += 1
        elif not resolvers._active(user):
            excluded["not_active"] += 1
        elif not roles & set(ENTERPRISE_ADMIN_ROLES):
            excluded["role_not_enterprise_admin"] += 1
        else:
            resolved.add(user_id)
    result.update(
        users_scanned=len(users),
        roles_seen=dict(roles_seen),
        resolved_user_ids=sorted(str(u) for u in resolved),
        excluded=dict(excluded),
    )
    return result

