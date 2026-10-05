"""Event-approval notifications using the platform notification pipeline."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enterprise_model import Enterprise
from app.repository.event_repo import event_owner_tenant_id
from app.services.invigorate_auth_client import list_tenant_users, list_tenants
from app.services.notification_service import create_automatic_notification
from app.services.super_admin_identity import profile_is_super_admin, profile_status_is_active


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


def _user_id(user: dict) -> UUID | None:
    for value in _nested_values(user):
        for field in ("user_id", "id", "keycloak_id"):
            raw_id = value.get(field)
            if raw_id:
                try:
                    return UUID(str(raw_id))
                except (TypeError, ValueError):
                    continue
    return None


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


def _active(user: dict) -> bool:
    return all(profile_status_is_active(value) for value in _nested_values(user))


def resolve_platform_admin_user_ids() -> list[UUID]:
    """Find active platform super-admin identities from existing tenant users.

    The identity service exposes tenant membership through the already-used
    internal tenant-users endpoint.  Platform administrators are identified by
    the same ``isSuperAdmin`` / ``super_admin`` identity rules used for Event
    authorization; ordinary tenant admins are deliberately excluded.
    """
    recipients: set[UUID] = set()
    seen_tenants: set[UUID] = set()
    for tenant in list_tenants():
        if not isinstance(tenant, dict):
            continue
        try:
            tenant_id = UUID(str(tenant.get("id")))
        except (TypeError, ValueError):
            continue
        if tenant_id in seen_tenants:
            continue
        seen_tenants.add(tenant_id)
        for user in list_tenant_users(tenant_id):
            user_id = _user_id(user)
            is_platform_admin = any(
                profile_is_super_admin(value) and profile_status_is_active(value)
                for value in _nested_values(user)
            )
            if user_id and _active(user) and is_platform_admin:
                recipients.add(user_id)
    return sorted(recipients, key=str)


def resolve_enterprise_admin_user_ids(db: Session, event) -> tuple[list[UUID], UUID | None]:
    """Return only owning-tenant Enterprise Admins, never arbitrary members."""
    enterprise = getattr(event, "enterprise", None)
    if enterprise is None and getattr(event, "enterprise_id", None):
        enterprise = db.query(Enterprise).filter(Enterprise.id == event.enterprise_id).first()

    tenant_raw = getattr(enterprise, "tenant_id", None) or event_owner_tenant_id(event)
    try:
        tenant_id = UUID(str(tenant_raw)) if tenant_raw else None
    except (TypeError, ValueError):
        tenant_id = None
    if tenant_id is None:
        return [], None

    recipients: set[UUID] = set()
    for user in list_tenant_users(tenant_id):
        user_id = _user_id(user)
        # Invigorate maps tenant_owner to the marketplace's Enterprise Admin
        # role.  Do not include tenant_admin/provider or ordinary members.
        if user_id and _active(user) and _roles(user) & {"admin", "tenant_owner"}:
            recipients.add(user_id)
    return sorted(recipients, key=str), tenant_id


def notify_event_approval_workflow(db: Session, event, *, previous_status: str, reason: str | None = None):
    """Persist and deliver one notification for a successful approval transition."""
    notification_type = _EVENT_NOTIFICATION_TYPES.get(event.status)
    if notification_type is None:
        return None

    if event.status == "pending_approval":
        recipients = resolve_platform_admin_user_ids()
        tenant_id = None
    else:
        recipients, tenant_id = resolve_enterprise_admin_user_ids(db, event)

    # The status-transition graph is the workflow's idempotency guard: a
    # repeated request cannot re-enter the same target status.  Avoid creating
    # an orphaned feed record when no correctly scoped recipient can be found.
    if not recipients:
        logger.warning(
            "%s notification NOT sent for event %s: no eligible recipients "
            "(invigorate_internal_api_configured=%s). "
            "GET /api/v1/admin/notifications/diagnostics?event_id=%s shows what resolved.",
            notification_type, event.id, settings.invigorate_internal_api_configured, event.id,
        )
        return None

    metadata = {
        "event_id": str(event.id),
        "entity_type": "event",
        "entity_id": str(event.id),
        "status": event.status,
        "reason": reason if event.status in {"rejected", "needs_revision"} else None,
    }
    event_title = getattr(event, "title", None) or "Event"
    titles_and_messages = {
        "event_submitted": ("Event submitted for approval", f'"{event_title}" was submitted for approval.'),
        "event_approved": ("Event approved", f'"{event_title}" has been approved.'),
        "event_rejected": ("Event rejected", f'"{event_title}" has been rejected.'),
        "event_changes_requested": ("Event changes requested", f'Changes were requested for "{event_title}".'),
    }
    title, message = titles_and_messages[notification_type]
    return create_automatic_notification(
        db,
        title=title,
        message=message,
        category=notification_type,
        user_ids=recipients,
        tenant_id=tenant_id,
        metadata=metadata,
        channels=["in_app"],
    )
