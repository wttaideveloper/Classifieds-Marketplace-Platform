"""Who is told / who may join, from the identity provider (communication's part of app/services/event_notification_service.py)."""
from mskit.auth.recipients import (  # noqa: F401 - the monolith's names
    _active, _application_user_id, _lookup, _nested_values, _roles, _user_id, list_tenant_users,
    owning_tenant_users, resolve_platform_admin_user_ids, resolve_tenant_admin_user_ids,
)


def resolve_enterprise_admin_user_ids(db, record, *, access_token=None):
    enterprise = getattr(record, "enterprise", None)
    if enterprise is None and getattr(record, "enterprise_id", None):
        from communication_service.models.enterprise_model import Enterprise

        enterprise = db.query(Enterprise).filter(Enterprise.id == record.enterprise_id).first()
    tenant = getattr(enterprise, "tenant_id", None) or getattr(record, "tenant_id", None)
    return resolve_tenant_admin_user_ids(tenant, access_token=access_token)
