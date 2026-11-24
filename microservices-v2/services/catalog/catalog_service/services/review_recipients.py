"""Who is told about a product/service review (catalog's part of app/services/event_notification_service.py).

The item's owning tenant comes from catalog's own data (enterprise.tenant_id, else the item's tenant_id, as
event_owner_tenant_id did); the people come from the identity provider (kit)."""
from mskit.auth.recipients import resolve_platform_admin_user_ids, resolve_tenant_admin_user_ids  # noqa: F401


def resolve_enterprise_admin_user_ids(db, item, *, access_token=None):
    enterprise = getattr(item, "enterprise", None)
    if enterprise is None and getattr(item, "enterprise_id", None):
        from catalog_service.models.enterprise_model import Enterprise

        enterprise = db.query(Enterprise).filter(Enterprise.id == item.enterprise_id).first()
    tenant = getattr(enterprise, "tenant_id", None) or getattr(item, "tenant_id", None)         or (getattr(getattr(item, "enterprise", None), "tenant_id", None))
    return resolve_tenant_admin_user_ids(tenant, access_token=access_token)
