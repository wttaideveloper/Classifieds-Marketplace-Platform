"""Order notifications (push + in-app feed, through the platform pipeline), sent after the change is saved; a problem sending never fails the API call.

Customer:
    order_processing                the store started preparing it
    order_shipped                   shipped: carrier, tracking_number and tracking_url (when there is one) are in `data`
    order_delivered                 delivered (a refund can be asked for within ORDER_REFUND_WINDOW_DAYS)
    order_cancelled                 cancelled (by the customer, or a request approved, or by the store)
    order_cancellation_rejected     the store did not approve a cancellation request (the reason is in the feed only, never in the push)
Staff (the enterprise's admins):
    order_cancellation_requested    a customer asked to cancel a paid order
    order_cancelled                 a customer cancelled

`data` is a flat map of strings: category, order_id, entity_type = "order", entity_id (= order id), status, and for shipping carrier / tracking_number / tracking_url.
Refund notifications are the payments module's (refund_requested / approved / rejected / completed).
"""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.services import notification_idempotency

logger = logging.getLogger(__name__)


def _label(order) -> str:
    return f"order #{str(order.id)[:8].upper()}"


def payload(order, category: str, **extra) -> dict[str, str]:
    data = {"category": category, "order_id": order.id, "entity_type": "order", "entity_id": order.id, "status": order.status, **extra}
    return {k: str(v) for k, v in data.items() if v is not None and v != ""}


def _deliver(db: Session, users, category: str, title: str, body: str, data: dict, key: str, tenant_id=None) -> bool:
    ids = []
    for u in dict.fromkeys(users or []):
        try:
            ids.append(UUID(str(u)))
        except (ValueError, TypeError):
            continue
    if not ids:
        return False
    try:
        if not notification_idempotency.claim(db, key):
            return False
        from app.services.notification_service import create_automatic_notification

        create_automatic_notification(db, title=title, message=body, category=category, user_ids=ids, tenant_id=tenant_id, metadata=data, channels=["in_app", "push"])
        return True
    except Exception:
        logger.exception("order notification %s failed", category)
        try:
            db.rollback()
            notification_idempotency.release(db, key)
        except Exception:
            pass
        return False


def _admins(db: Session, order) -> list:
    try:
        from app.services.event_notification_service import resolve_enterprise_admin_user_ids

        found, _tenant = resolve_enterprise_admin_user_ids(db, order)
        return [str(u) for u in found]
    except Exception:
        logger.debug("could not list the admins of order %s", order.id, exc_info=True)
        return []


def send(db: Session, order, kind: str, *, by_customer: bool = False) -> None:
    stamp = (order.updated_at or order.created_at).replace(microsecond=0).isoformat()
    label = _label(order)
    if kind == "processing":
        category, title, body = "order_processing", "Order being prepared", f"The store is preparing your {label}."
        data = payload(order, category)
    elif kind == "shipped":
        category, title = "order_shipped", "Order shipped"
        body = f"Your {label} is on its way with {order.carrier}. Tracking number: {order.tracking_number}."
        data = payload(order, category, carrier=order.carrier, tracking_number=order.tracking_number, tracking_url=order.tracking_url)
    elif kind == "delivered":
        category, title, body = "order_delivered", "Order delivered", f"Your {label} was delivered."
        data = payload(order, category)
    elif kind == "cancelled":
        category, title, body = "order_cancelled", "Order cancelled", f"Your {label} was cancelled."
        data = payload(order, category, reason=order.cancel_reason if order.cancelled_by_role != "customer" else None)
        if by_customer:
            _deliver(db, _admins(db, order), category, "Order cancelled", f"A customer cancelled {label}.", payload(order, category), f"order_staff:{order.id}:{category}:{stamp}", order.tenant_id)
    elif kind == "cancellation_rejected":
        category, title, body = "order_cancellation_rejected", "Cancellation not approved", f"The store did not approve your request to cancel {label}."
        data = payload(order, category, reason=order.decision_reason)
    elif kind == "cancellation_requested":
        category = "order_cancellation_requested"
        _deliver(db, _admins(db, order), category, "Cancellation requested", f"A customer asked to cancel {label}.", payload(order, category, reason=order.cancel_reason),
                 f"order_staff:{order.id}:{category}:{stamp}", order.tenant_id)
        return
    else:
        return
    _deliver(db, [order.user_id], category, title, body, data, f"order:{order.id}:{category}:{stamp}", order.tenant_id)
