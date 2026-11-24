"""Who is told about orders and payments (commerce's part of app/services/event_notification_service.py)."""
from mskit.auth.recipients import resolve_platform_admin_user_ids, resolve_tenant_admin_user_ids  # noqa: F401


def resolve_enterprise_admin_user_ids(db, record, *, access_token=None):
    """The record's owning tenant: its enterprise's (read-only replica) or its own tenant_id, as event_owner_tenant_id did."""
    enterprise = getattr(record, "enterprise", None)
    if enterprise is None and getattr(record, "enterprise_id", None):
        from commerce_service.models.enterprise_model import Enterprise

        enterprise = db.query(Enterprise).filter(Enterprise.id == record.enterprise_id).first()
    tenant = getattr(enterprise, "tenant_id", None) or getattr(record, "tenant_id", None)
    return resolve_tenant_admin_user_ids(tenant, access_token=access_token)
