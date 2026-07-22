import logging
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.repository import notification_repo
from app.repository.query_utils import build_pagination_meta
from app.schemas.notification_schema import (
    NotificationCreate,
    NotificationTemplateCreate,
    NotificationTemplateUpdate,
    NotificationUpdate,
    ScheduleNotificationRequest,
    SendNotificationRequest,
    SendToGroupsRequest,
    SendToTenantRequest,
    SendToUsersRequest,
)
from app.services.invigorate_auth_client import list_tenant_user_ids
from app.services.notification_delivery_service import deliver_notification_to_users

logger = logging.getLogger(__name__)
debug_logger = logging.getLogger("notification_debug")


def _parse_user_id(current_user: dict) -> UUID:
    return UUID(str(current_user["id"]))


def _parse_tenant_id(current_user: dict) -> UUID | None:
    raw = current_user.get("tenant_id")
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        return None


def _require_super_admin(current_user: dict) -> None:
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Super admin access required.")


def _require_tenant_admin(current_user: dict) -> None:
    if current_user.get("role") not in {"admin", "provider"}:
        raise HTTPException(status_code=403, detail="Tenant admin access required.")


def _map_user_notification(row, notification) -> dict:
    return {
        "id": row.id,
        "notification_id": row.notification_id,
        "user_id": row.user_id,
        "is_read": row.is_read,
        "read_at": row.read_at,
        "delivered_at": row.delivered_at,
        "title": notification.title,
        "message": notification.message,
        "notification_type": notification.notification_type,
        "category": notification.category,
        "metadata": notification.metadata_json or {},
        "created_at": notification.created_at,
    }


def create_notification_service(db: Session, current_user: dict, payload: NotificationCreate):
    debug_logger.info(
        "[NOTIF_DEBUG] POST /notifications (create) called by user_id=%s role=%s tenant_id_claim=%s payload_tenant_id=%s",
        current_user.get("id"), current_user.get("role"), current_user.get("tenant_id"), payload.tenant_id,
    )
    _require_tenant_admin(current_user)
    tenant_id = payload.tenant_id or _parse_tenant_id(current_user)
    if current_user.get("role") == "provider" and tenant_id != _parse_tenant_id(current_user):
        raise HTTPException(status_code=403, detail="Cannot create notifications for another tenant.")

    if tenant_id is None:
        # No tenant context to resolve recipients from (e.g. platform super admin) -
        # fall back to a recipient-less draft, matching prior behavior.
        row = notification_repo.create_notification(
            db,
            tenant_id=tenant_id,
            created_by=_parse_user_id(current_user),
            title=payload.title,
            message=payload.message,
            notification_type=payload.notification_type,
            category=payload.category,
            delivery_type=payload.delivery_type,
            scheduled_at=payload.scheduled_at,
            status="scheduled" if payload.scheduled_at else "draft",
            metadata=payload.metadata,
        )
        return notification_repo._map_notification(row)

    user_ids = list_tenant_user_ids(tenant_id)
    debug_logger.info(
        "[NOTIF_DEBUG] create_notification_service resolved tenant_id=%s recipient_user_ids=%s (count=%s)",
        tenant_id, [str(u) for u in user_ids], len(user_ids),
    )
    if not user_ids:
        raise HTTPException(
            status_code=400,
            detail=(
                "No tenant users resolved. Configure INVIGORATE_AUTH_BASE_URL and "
                "INVIGORATE_INTERNAL_API_KEY, or use POST /notifications/send-to-users."
            ),
        )

    result = _dispatch_notification(
        db,
        current_user,
        title=payload.title,
        message=payload.message,
        notification_type=payload.notification_type,
        category=payload.category,
        user_ids=user_ids,
        tenant_id=tenant_id,
        channels=["in_app", "push"],
        metadata=payload.metadata,
        delivery_type=payload.delivery_type,
        scheduled_at=payload.scheduled_at,
    )
    row = notification_repo.get_notification_by_id(db, result["notification_id"])
    return notification_repo._map_notification(row)


def list_notifications_service(
    db: Session,
    current_user: dict,
    *,
    tenant_id: UUID | None,
    status: str | None,
    notification_type: str | None,
    category: str | None,
    page: int,
    page_size: int,
):
    _require_tenant_admin(current_user)
    scoped_tenant = tenant_id
    if current_user.get("role") == "provider":
        scoped_tenant = _parse_tenant_id(current_user)

    rows, total = notification_repo.list_notifications(
        db,
        tenant_id=scoped_tenant,
        status=status,
        notification_type=notification_type,
        category=category,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [notification_repo._map_notification(row) for row in rows],
        "pagination": build_pagination_meta(total, page, page_size),
    }


def get_notification_service(db: Session, current_user: dict, notification_id: UUID):
    _require_tenant_admin(current_user)
    row = notification_repo.get_notification_by_id(db, notification_id)
    if not row:
        raise HTTPException(status_code=404, detail="Notification not found")
    if current_user.get("role") == "provider" and row.tenant_id != _parse_tenant_id(current_user):
        raise HTTPException(status_code=403, detail="Not authorized for this notification.")
    return notification_repo._map_notification(row)


def update_notification_service(
    db: Session,
    current_user: dict,
    notification_id: UUID,
    payload: NotificationUpdate,
):
    row = notification_repo.get_notification_by_id(db, notification_id)
    if not row:
        raise HTTPException(status_code=404, detail="Notification not found")
    _require_tenant_admin(current_user)
    if current_user.get("role") == "provider" and row.tenant_id != _parse_tenant_id(current_user):
        raise HTTPException(status_code=403, detail="Not authorized for this notification.")
    updated = notification_repo.update_notification(
        db,
        row,
        payload.model_dump(exclude_unset=True),
    )
    return notification_repo._map_notification(updated)


def delete_notification_service(db: Session, current_user: dict, notification_id: UUID):
    row = notification_repo.get_notification_by_id(db, notification_id)
    if not row:
        raise HTTPException(status_code=404, detail="Notification not found")
    _require_tenant_admin(current_user)
    if current_user.get("role") == "provider" and row.tenant_id != _parse_tenant_id(current_user):
        raise HTTPException(status_code=403, detail="Not authorized for this notification.")
    notification_repo.delete_notification(db, row)
    return {"message": "Notification deleted successfully"}


def _dispatch_notification(
    db: Session,
    current_user: dict,
    *,
    title: str,
    message: str,
    notification_type: str,
    category: str,
    user_ids: list[UUID],
    tenant_id: UUID | None,
    channels: list[str],
    metadata: dict,
    delivery_type: str = "immediate",
    scheduled_at: datetime | None = None,
):
    debug_logger.info(
        "[NOTIF_DEBUG] _dispatch_notification called by user_id=%s title=%r tenant_id=%s "
        "user_ids=%s (count=%s) delivery_type=%s scheduled_at=%s channels=%s",
        current_user.get("id"), title, tenant_id,
        [str(u) for u in user_ids], len(user_ids),
        delivery_type, scheduled_at, channels,
    )
    if not user_ids:
        raise HTTPException(status_code=400, detail="No recipients resolved for this notification.")

    row = notification_repo.create_notification(
        db,
        tenant_id=tenant_id,
        created_by=_parse_user_id(current_user),
        title=title,
        message=message,
        notification_type=notification_type,
        category=category,
        delivery_type=delivery_type,
        scheduled_at=scheduled_at,
        status="scheduled" if scheduled_at else "processing",
        metadata=metadata,
    )

    if scheduled_at:
        schedule_metadata = {
            **metadata,
            "recipient_user_ids": [str(uid) for uid in user_ids],
            "channels": channels,
        }
        notification_repo.update_notification(
            db,
            row,
            {"status": "scheduled", "metadata": schedule_metadata},
        )
        try:
            from app.tasks.notification_tasks import deliver_scheduled_notification_task

            deliver_scheduled_notification_task.apply_async(
                args=[str(row.id), [str(uid) for uid in user_ids], channels],
                eta=scheduled_at,
            )
        except Exception:
            logger.exception("Celery unavailable; scheduled notification stored only.")
        return {
            "notification_id": row.id,
            "recipients": len(user_ids),
            "delivered": 0,
            "status": "scheduled",
        }

    delivered = deliver_notification_to_users(
        db,
        notification_id=row.id,
        title=title,
        body=message,
        user_ids=user_ids,
        channels=channels,
        metadata=metadata,
    )
    notification_repo.update_notification(db, row, {"status": "sent" if delivered else "failed"})
    return {
        "notification_id": row.id,
        "recipients": len(user_ids),
        "delivered": delivered,
        "status": "sent" if delivered else "failed",
    }


def send_notification_service(db: Session, current_user: dict, payload: SendNotificationRequest):
    debug_logger.info(
        "[NOTIF_DEBUG] POST /notifications/send called by user_id=%s payload_user_ids=%s",
        current_user.get("id"), [str(u) for u in payload.user_ids],
    )
    _require_tenant_admin(current_user)
    tenant_id = payload.tenant_id or _parse_tenant_id(current_user)
    return _dispatch_notification(
        db,
        current_user,
        title=payload.title,
        message=payload.message,
        notification_type=payload.notification_type,
        category=payload.category,
        user_ids=payload.user_ids,
        tenant_id=tenant_id,
        channels=payload.channels,
        metadata=payload.metadata,
    )


def schedule_notification_service(db: Session, current_user: dict, payload: ScheduleNotificationRequest):
    _require_tenant_admin(current_user)
    tenant_id = payload.tenant_id or _parse_tenant_id(current_user)
    return _dispatch_notification(
        db,
        current_user,
        title=payload.title,
        message=payload.message,
        notification_type=payload.notification_type,
        category=payload.category,
        user_ids=payload.user_ids,
        tenant_id=tenant_id,
        channels=payload.channels,
        metadata=payload.metadata,
        delivery_type="scheduled",
        scheduled_at=payload.scheduled_at,
    )


def send_to_tenant_service(db: Session, current_user: dict, payload: SendToTenantRequest):
    debug_logger.info(
        "[NOTIF_DEBUG] POST /notifications/send-to-tenant called by user_id=%s tenant_id=%s",
        current_user.get("id"), payload.tenant_id,
    )
    if current_user.get("role") == "provider":
        own_tenant = _parse_tenant_id(current_user)
        if own_tenant and own_tenant != payload.tenant_id:
            raise HTTPException(status_code=403, detail="Cannot send to another tenant.")
    else:
        _require_super_admin(current_user)

    user_ids = list_tenant_user_ids(payload.tenant_id)
    if not user_ids:
        raise HTTPException(
            status_code=400,
            detail=(
                "No tenant users resolved. Configure INVIGORATE_AUTH_BASE_URL and "
                "INVIGORATE_INTERNAL_API_KEY, or use POST /notifications/send-to-users."
            ),
        )
    return _dispatch_notification(
        db,
        current_user,
        title=payload.title,
        message=payload.message,
        notification_type=payload.notification_type,
        category=payload.category,
        user_ids=user_ids,
        tenant_id=payload.tenant_id,
        channels=payload.channels,
        metadata=payload.metadata,
    )


def send_to_users_service(db: Session, current_user: dict, payload: SendToUsersRequest):
    debug_logger.info(
        "[NOTIF_DEBUG] POST /notifications/send-to-users called by user_id=%s payload_user_ids=%s",
        current_user.get("id"), [str(u) for u in payload.user_ids],
    )
    _require_tenant_admin(current_user)
    tenant_id = payload.tenant_id or _parse_tenant_id(current_user)
    return _dispatch_notification(
        db,
        current_user,
        title=payload.title,
        message=payload.message,
        notification_type=payload.notification_type,
        category=payload.category,
        user_ids=payload.user_ids,
        tenant_id=tenant_id,
        channels=payload.channels,
        metadata=payload.metadata,
    )


def send_to_groups_service(db: Session, current_user: dict, payload: SendToGroupsRequest):
    debug_logger.info(
        "[NOTIF_DEBUG] POST /notifications/send-to-groups called by user_id=%s payload_group_user_ids=%s",
        current_user.get("id"), [str(u) for u in payload.group_user_ids],
    )
    _require_tenant_admin(current_user)
    tenant_id = payload.tenant_id or _parse_tenant_id(current_user)
    return _dispatch_notification(
        db,
        current_user,
        title=payload.title,
        message=payload.message,
        notification_type=payload.notification_type,
        category=payload.category,
        user_ids=payload.group_user_ids,
        tenant_id=tenant_id,
        channels=payload.channels,
        metadata=payload.metadata,
    )


def list_my_notifications_service(
    db: Session,
    current_user: dict,
    *,
    page: int,
    page_size: int,
):
    user_id = _parse_user_id(current_user)
    debug_logger.info(
        "[NOTIF_DEBUG] GET /users/me/notifications called raw_current_user=%s parsed_user_id=%s",
        {k: v for k, v in current_user.items() if k != "email"}, user_id,
    )
    rows, total = notification_repo.list_user_notifications(
        db,
        user_id,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [_map_user_notification(row, notification) for row, notification in rows],
        "pagination": build_pagination_meta(total, page, page_size),
    }


def mark_my_notification_read_service(db: Session, current_user: dict, user_notification_id: UUID):
    user_id = _parse_user_id(current_user)
    result = notification_repo.get_user_notification(db, user_id, user_notification_id)
    if not result:
        raise HTTPException(status_code=404, detail="Notification not found")
    row, _notification = result
    updated = notification_repo.mark_user_notification_read(db, row)
    return {
        "id": updated.id,
        "is_read": updated.is_read,
        "read_at": updated.read_at,
    }


def mark_all_my_notifications_read_service(db: Session, current_user: dict):
    user_id = _parse_user_id(current_user)
    marked = notification_repo.mark_all_user_notifications_read(db, user_id)
    return {"marked_read": marked}


def my_unread_count_service(db: Session, current_user: dict):
    user_id = _parse_user_id(current_user)
    return {"unread_count": notification_repo.count_unread_user_notifications(db, user_id)}


def create_template_service(db: Session, current_user: dict, payload: NotificationTemplateCreate):
    _require_tenant_admin(current_user)
    tenant_id = payload.tenant_id or _parse_tenant_id(current_user)
    if current_user.get("role") == "provider" and tenant_id != _parse_tenant_id(current_user):
        raise HTTPException(status_code=403, detail="Cannot create templates for another tenant.")
    row = notification_repo.create_template(
        db,
        tenant_id=tenant_id,
        template_name=payload.template_name,
        category=payload.category,
        title=payload.title,
        message=payload.message,
        is_active=payload.is_active,
    )
    return row


def list_templates_service(
    db: Session,
    current_user: dict,
    *,
    tenant_id: UUID | None,
    active_only: bool,
    page: int,
    page_size: int,
):
    _require_tenant_admin(current_user)
    scoped_tenant = tenant_id
    if current_user.get("role") == "provider":
        scoped_tenant = _parse_tenant_id(current_user)
    rows, total = notification_repo.list_templates(
        db,
        tenant_id=scoped_tenant,
        active_only=active_only,
        page=page,
        page_size=page_size,
    )
    return {
        "items": rows,
        "pagination": build_pagination_meta(total, page, page_size),
    }


def update_template_service(
    db: Session,
    current_user: dict,
    template_id: UUID,
    payload: NotificationTemplateUpdate,
):
    _require_tenant_admin(current_user)
    row = notification_repo.get_template_by_id(db, template_id)
    if not row:
        raise HTTPException(status_code=404, detail="Template not found")
    if current_user.get("role") == "provider" and row.tenant_id != _parse_tenant_id(current_user):
        raise HTTPException(status_code=403, detail="Not authorized for this template.")
    return notification_repo.update_template(db, row, payload.model_dump(exclude_unset=True))


def delete_template_service(db: Session, current_user: dict, template_id: UUID):
    _require_tenant_admin(current_user)
    row = notification_repo.get_template_by_id(db, template_id)
    if not row:
        raise HTTPException(status_code=404, detail="Template not found")
    if current_user.get("role") == "provider" and row.tenant_id != _parse_tenant_id(current_user):
        raise HTTPException(status_code=403, detail="Not authorized for this template.")
    notification_repo.delete_template(db, row)
    return {"message": "Template deleted successfully"}


def create_automatic_notification(
    db: Session,
    *,
    title: str,
    message: str,
    category: str,
    user_ids: list[UUID],
    tenant_id: UUID | None = None,
    metadata: dict | None = None,
    channels: list[str] | None = None,
):
    """Helper for automatic triggers (new message, booking confirmed, etc.)."""
    if not user_ids:
        return None
    row = notification_repo.create_notification(
        db,
        tenant_id=tenant_id,
        created_by=None,
        title=title,
        message=message,
        notification_type="automatic",
        category=category,
        delivery_type="immediate",
        scheduled_at=None,
        status="processing",
        metadata=metadata or {},
    )
    delivered = deliver_notification_to_users(
        db,
        notification_id=row.id,
        title=title,
        body=message,
        user_ids=user_ids,
        channels=channels or ["in_app", "push"],
        metadata=metadata,
    )
    notification_repo.update_notification(db, row, {"status": "sent" if delivered else "failed"})
    return row
