"""Event-approval notifications using the platform notification pipeline."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enterprise_model import Enterprise
from app.repository.event_repo import event_owner_tenant_id
from app.services.invigorate_auth_client import (
    fetch_tenant_me_profile,
    list_super_admins,
    list_tenant_members,
    list_tenant_users,
    member_application_user_id,
)
from app.services import notification_idempotency, training_notifications
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


def resolve_enterprise_admin_user_ids(
    db: Session, event, *, access_token: str | None = None
) -> tuple[list[UUID], UUID | None]:
    """Return only owning-tenant Enterprise Admins, never arbitrary members.

    The owning tenant always comes from the Event. If the caller's token belongs to that
    tenant, members come from ``GET /api/v1/tenant/members``; otherwise (Super Admin acting
    on another tenant's event) from Identity ``GET /api/v1/internal/tenants/{owning_tenant_id}/users``
    with ``X-Internal-Api-Key`` + the Bearer token.
    """
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

    users, _source = owning_tenant_users(tenant_id, access_token)

    recipients: set[UUID] = set()
    for user in users:
        user_id = _application_user_id(user)
        # Invigorate maps tenant_owner to the marketplace's Enterprise Admin
        # role.  Do not include tenant_admin/provider or ordinary members.
        if user_id and _active(user) and _roles(user) & {"admin", "tenant_owner"}:
            recipients.add(user_id)
    return sorted(recipients, key=str), tenant_id


def notify_event_approval_workflow(
    db: Session,
    event,
    *,
    previous_status: str,
    reason: str | None = None,
    transition_id: UUID | None = None,
    access_token: str | None = None,
):
    """Persist and deliver one notification for a successful approval transition."""
    notification_type = _EVENT_NOTIFICATION_TYPES.get(event.status)
    if notification_type is None:
        return None

    if event.status == "pending_approval":
        recipients = resolve_platform_admin_user_ids(access_token=access_token)
        tenant_id = None
    else:
        recipients, tenant_id = resolve_enterprise_admin_user_ids(db, event, access_token=access_token)

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
        "category": notification_type,
        "event_id": str(event.id),
        "entity_type": "event",
        "entity_id": str(event.id),
        "status": event.status,
    }
    if reason and event.status in {"rejected", "needs_revision"}:
        metadata["reason"] = reason
    event_title = getattr(event, "title", None) or "Event"
    titles_and_messages = {
        "event_submitted": ("Event submitted for approval", f'"{event_title}" was submitted for approval.'),
        "event_approved": ("Event approved", f'"{event_title}" has been approved.'),
        "event_rejected": ("Event rejected", f'"{event_title}" has been rejected.'),
        "event_changes_requested": ("Event changes requested", f'Changes were requested for "{event_title}".'),
    }
    # A committed audit row identifies one real state transition.  It lets a retry
    # be silent without preventing a later resubmission/review from notifying again.
    dedupe_key = f"{notification_type}:{event.id}:{transition_id or f'{previous_status}->{event.status}'}"
    if not notification_idempotency.claim(db, dedupe_key):
        logger.info("%s skipped for event %s: transition already delivered", notification_type, event.id)
        return None

    title, message = titles_and_messages[notification_type]
    try:
        return create_automatic_notification(
            db,
            title=title,
            message=message,
            category=notification_type,
            user_ids=recipients,
            tenant_id=tenant_id,
            metadata=training_notifications._routing_metadata(notification_type, metadata),
            channels=["in_app"],
        )
    except Exception:
        logger.exception(
            "%s notification failed for event %s recipients=%s",
            notification_type,
            event.id,
            [str(user_id) for user_id in recipients],
        )
        db.rollback()
        notification_idempotency.release(db, dedupe_key)
        return None
