"""Who receives platform notifications, from the identity provider (Invigorate) only - no database.

Copied from app/services/event_notification_service.py (helpers + resolve_platform_admin_user_ids),
which every domain used to find Super Admins.
"""


from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from mskit.settings import settings
from mskit.auth.invigorate_auth_client import (
    fetch_tenant_me_profile,
    list_super_admins,
    list_tenant_members,
    list_tenant_users,
    member_application_user_id,
)
from mskit.auth.super_admin_identity import profile_is_super_admin, profile_status_is_active


logger = logging.getLogger(__name__)

_EVENT_NOTIFICATION_TYPES = {
    "pending_approval": "event_submitted",
    "approved": "event_approved",
    "rejected": "event_rejected",
    "needs_revision": "event_changes_requested",
}


def _nested_values(user: dict) -> list[dict]:
    values = [user]
    for key in ("data", "user", "profile"):
        nested = user.get(key)
        if isinstance(nested, dict):
            values.append(nested)
    return values


def _application_user_id(user: dict) -> UUID | None:
    """Return the recipient's application ``users.id``, never its Keycloak subject.

    Documented member records expose it as ``userId`` (their ``id``/``membershipId`` is the
    membership record id) and super-admin records as ``id``. ``keycloakId`` / ``keycloak_id``
    are authentication identities and are never accepted: ``user_notifications.user_id`` is
    the application-user UUID used by the authenticated feed APIs.
    """
    for value in _nested_values(user):
        user_id = member_application_user_id(value)
        if user_id is not None:
            return user_id
    return None


# Backward-compatible name kept for the training learner-identity service, which still resolves
# ids from tenant member records through this module (`members._user_id`). Same rules as above.
_user_id = _application_user_id


def _roles(user: dict) -> set[str]:
    roles: set[str] = set()
    for value in _nested_values(user):
        for field in ("role", "user_role", "tenant_role", "marketplace_role"):
            raw_role = value.get(field)
            if raw_role:
                roles.add(str(raw_role).strip().lower())
        for field in ("tenant_rbac_roles", "roles"):
            raw_roles = value.get(field)
            if isinstance(raw_roles, (list, tuple, set)):
                roles.update(str(role).strip().lower() for role in raw_roles if role)
    return roles


_STATUS_FIELDS = ("status", "membershipStatus", "userStatus")


def _active(user: dict) -> bool:
    """Active only if EVERY status field the record carries is active (an absent field is
    not evidence of inactivity, but any present non-active one excludes the user)."""
    for value in _nested_values(user):
        for field in _STATUS_FIELDS:
            if field in value and not profile_status_is_active({"status": value[field]}):
                return False
    return True


def _lookup(fn, *args, access_token: str | None = None):
    """Call an identity lookup (e.g. ``list_tenant_users``), forwarding the caller's Bearer token when
    there is one. Identity endpoints take the token only when the caller supplied it, so lookups made
    before any authentication (system jobs, token-less flows) behave exactly as they always did."""
    return fn(*args, access_token=access_token) if access_token else fn(*args)


def resolve_platform_admin_user_ids(access_token: str | None = None) -> list[UUID]:
    """Active Platform Super Admin application user ids.

    Source: Identity API ``GET /api/v1/internal/super-admins`` (``X-Internal-Api-Key`` +
    the caller's Bearer token). Each record's ``id`` is the application user id;
    ``keycloakId`` is ignored. Ordinary tenant admins are not in this list, and a record
    must also carry ``isSuperAdmin`` and an active status.
    """
    recipients: set[UUID] = set()
    for record in list_super_admins(access_token):
        if not profile_is_super_admin(record):
            continue
        user_id = member_application_user_id(record)
        if user_id and _active(record):
            recipients.add(user_id)
    return sorted(recipients, key=str)




def owning_tenant_users(tenant_id: UUID, access_token: str | None) -> tuple[list[dict], str]:
    """Members of the OWNING tenant, plus which documented endpoint supplied them.

    If the caller's token belongs to that tenant, ``GET /api/v1/tenant/members`` is used.
    Otherwise (e.g. a Platform Super Admin acting on another tenant's event), or if that
    lookup fails, Identity ``GET /api/v1/internal/tenants/{tenant_id}/users`` is used with
    ``X-Internal-Api-Key`` + the Bearer token. The caller's own tenant is never queried
    on behalf of another tenant's event.
    """
    if access_token:
        profile = fetch_tenant_me_profile(access_token)
        try:
            token_tenant = UUID(str((profile or {}).get("id")))
        except (TypeError, ValueError):
            token_tenant = None
        if token_tenant == tenant_id:
            members = list_tenant_members(access_token)
            if members is not None:
                return members, "tenant/members"
    return list_tenant_users(tenant_id, access_token), "internal/tenants/{tenant_id}/users"


def resolve_tenant_admin_user_ids(tenant_id, *, access_token: str | None = None) -> tuple[list[UUID], UUID | None]:
    """Second half of the monolith's resolve_enterprise_admin_user_ids: the owning tenant's Enterprise Admins
    (tenant_owner / admin, active) from the identity provider. The caller - the service that owns the item -
    supplies the tenant (enterprise.tenant_id, or the item's own tenant)."""
    try:
        tenant_id = UUID(str(tenant_id)) if tenant_id else None
    except (TypeError, ValueError):
        tenant_id = None
    if tenant_id is None:
        return [], None

    users, _source = owning_tenant_users(tenant_id, access_token)

    recipients: set[UUID] = set()
    for user in users:
        user_id = _application_user_id(user)
        # Invigorate maps tenant_owner to the marketplace's Enterprise Admin
        # role.  Do not include tenant_admin/provider or ordinary members.
        if user_id and _active(user) and _roles(user) & {"admin", "tenant_owner"}:
            recipients.add(user_id)
    return sorted(recipients, key=str), tenant_id
