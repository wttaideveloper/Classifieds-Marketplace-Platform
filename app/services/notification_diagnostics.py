"""Read-only view of *why* a workflow notification did or did not reach someone.

Event/Training approval and enrollment notifications resolve their recipients from the Invigorate
tenant-user listing at send time. When that listing is unavailable or the roles/ids don't match, the
workflow quietly sends nothing — this reports exactly what the resolvers see, so a deployment can be
checked without guessing. Counts and ids only: no emails, names or secrets.
"""
from collections import Counter
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enterprise_model import Enterprise
from app.models.notification_model import Notification, UserNotification
from app.repository.event_repo import event_owner_tenant_id
from app.services import event_notification_service as resolvers
from app.services.super_admin_identity import profile_is_super_admin

ENTERPRISE_ADMIN_ROLES = ("admin", "tenant_owner")


def diagnose_platform_admins() -> dict:
    configured = settings.invigorate_internal_api_configured
    tenants = resolvers.list_tenants() if configured else []
    excluded: Counter = Counter()
    resolved: set[UUID] = set()
    scanned = 0
    seen: set[str] = set()
    for tenant in tenants:
        tenant_id = tenant.get("id") if isinstance(tenant, dict) else None
        if not tenant_id or str(tenant_id) in seen:
            continue
        seen.add(str(tenant_id))
        try:
            users = resolvers.list_tenant_users(UUID(str(tenant_id)))
        except ValueError:
            continue
        for user in users:
            scanned += 1
            user_id = resolvers._user_id(user)
            flagged = any(profile_is_super_admin(v) for v in resolvers._nested_values(user))
            if user_id is None:
                excluded["no_usable_user_id"] += 1
            elif not flagged:
                excluded["not_flagged_super_admin"] += 1
            elif not resolvers._active(user):
                excluded["super_admin_not_active"] += 1
            else:
                resolved.add(user_id)
    return {
        "tenants_listed": len(seen),
        "users_scanned": scanned,
        "resolved_user_ids": sorted(str(u) for u in resolved),
        "excluded": dict(excluded),
    }


def diagnose_enterprise_admins(db: Session, entity) -> dict:
    enterprise = getattr(entity, "enterprise", None)
    if enterprise is None and getattr(entity, "enterprise_id", None):
        enterprise = db.query(Enterprise).filter(Enterprise.id == entity.enterprise_id).first()
    tenant_raw = getattr(enterprise, "tenant_id", None) or event_owner_tenant_id(entity)
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
    users = resolvers.list_tenant_users(tenant_id)
    excluded: Counter = Counter()
    roles_seen: Counter = Counter()
    resolved: set[UUID] = set()
    for user in users:
        user_id = resolvers._user_id(user)
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


def recorded_notifications(db: Session, entity_id: UUID) -> list[dict]:
    """Notification rows written for this entity and who they were addressed to."""
    rows = (
        db.query(Notification)
        .filter(Notification.metadata_json["entity_id"].astext == str(entity_id))
        .order_by(Notification.created_at.desc())
        .limit(50)
        .all()
    )
    out = []
    for n in rows:
        recipients = [
            str(u.user_id)
            for u in db.query(UserNotification).filter(UserNotification.notification_id == n.id).all()
        ]
        out.append({
            "notification_id": str(n.id),
            "category": n.category,
            "status": n.status,
            "created_at": n.created_at.isoformat() if n.created_at else None,
            "recipient_user_ids": recipients,
        })
    return out
