"""Who is told about a training (learning's part of app/services/event_notification_service.py).

Identity-provider lookups come from the kit; the owning tenant comes from the training's own enterprise
(read-only replica) or tenant, as event_owner_tenant_id did."""
from mskit.auth.recipients import (  # noqa: F401 - re-exported under the monolith's names
    _active, _application_user_id, _lookup, _nested_values, _roles, _user_id, list_tenant_users,
    owning_tenant_users, resolve_platform_admin_user_ids, resolve_tenant_admin_user_ids,
)


def resolve_enterprise_admin_user_ids(db, record, *, access_token=None):
    enterprise = getattr(record, "enterprise", None)
    if enterprise is None and getattr(record, "enterprise_id", None):
        from learning_service.models.enterprise_model import Enterprise

        enterprise = db.query(Enterprise).filter(Enterprise.id == record.enterprise_id).first()
    tenant = getattr(enterprise, "tenant_id", None) or getattr(record, "tenant_id", None)
    return resolve_tenant_admin_user_ids(tenant, access_token=access_token)
