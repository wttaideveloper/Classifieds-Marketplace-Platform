"""Patient order actions and tenant-scoped administration."""

from fastapi import HTTPException
from commerce_service.models.cart_model import Order


def locked_order(db, order_id, *, user_id=None, admin=None):
    query = db.query(Order).filter_by(id=order_id, is_deleted=False)
    if admin is None:
        query = query.filter_by(user_id=user_id)
    elif not admin.is_super_admin:
        if admin.role != "merchant" or admin.tenant_id is None:
            raise HTTPException(403, "Administrator access required")
        query = query.filter_by(tenant_id=admin.tenant_id)
    order = query.populate_existing().with_for_update().first()
    if order is None:
        raise HTTPException(404, "Order not found")
    return order
