import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.notification_model import (
    Notification,
    NotificationLog,
    NotificationTemplate,
    UserNotification,
)
from app.repository.query_utils import apply_pagination, build_pagination_meta

logger = logging.getLogger("notification_debug")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(_handler)
    logger.propagate = False


def _map_notification(row: Notification) -> dict:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "created_by": row.created_by,
        "title": row.title,
        "message": row.message,
        "notification_type": row.notification_type,
        "category": row.category,
        "delivery_type": row.delivery_type,
        "scheduled_at": row.scheduled_at,
        "status": row.status,
        "metadata": row.metadata_json or {},
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def create_notification(db: Session, **fields) -> Notification:
    metadata = fields.pop("metadata", None) or {}
    row = Notification(**fields, metadata_json=metadata)
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(
        "[NOTIF_DEBUG] created notification id=%s tenant_id=%s created_by=%s status=%s",
        row.id, row.tenant_id, row.created_by, row.status,
    )
    return row


def get_notification_by_id(db: Session, notification_id: UUID) -> Notification | None:
    return db.query(Notification).filter(Notification.id == notification_id).first()


def list_notifications(
    db: Session,
    *,
    tenant_id: UUID | None = None,
    status: str | None = None,
    notification_type: str | None = None,
    category: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Notification], int]:
    query = db.query(Notification)
    if tenant_id is not None:
        query = query.filter(Notification.tenant_id == tenant_id)
    if status:
        query = query.filter(Notification.status == status)
    if notification_type:
        query = query.filter(Notification.notification_type == notification_type)
    if category:
        query = query.filter(Notification.category == category)
    query = query.order_by(Notification.created_at.desc())
    total = query.count()
    rows = apply_pagination(query, page, page_size).all()
    return rows, total


def update_notification(db: Session, row: Notification, updates: dict) -> Notification:
    metadata = updates.pop("metadata", None)
    for key, value in updates.items():
        if value is not None:
            setattr(row, key, value)
    if metadata is not None:
        row.metadata_json = metadata
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


def delete_notification(db: Session, row: Notification) -> None:
    db.delete(row)
    db.commit()


def create_user_notifications(
    db: Session,
    notification_id: UUID,
    user_ids: list[UUID],
) -> list[UserNotification]:
    logger.info(
        "[NOTIF_DEBUG] create_user_notifications called notification_id=%s requested_user_ids=%s",
        notification_id, [str(u) for u in user_ids],
    )
    existing = {
        row.user_id
        for row in db.query(UserNotification.user_id)
        .filter(
            UserNotification.notification_id == notification_id,
            UserNotification.user_id.in_(user_ids),
        )
        .all()
    }
    created: list[UserNotification] = []
    now = datetime.utcnow()
    for user_id in user_ids:
        if user_id in existing:
            continue
        row = UserNotification(
            notification_id=notification_id,
            user_id=user_id,
            delivered_at=now,
        )
        db.add(row)
        created.append(row)
    db.commit()
    for row in created:
        db.refresh(row)
    logger.info(
        "[NOTIF_DEBUG] create_user_notifications committed notification_id=%s "
        "already_existing=%s newly_inserted=%s inserted_ids=%s",
        notification_id,
        [str(u) for u in existing],
        len(created),
        [str(row.id) for row in created],
    )
    return created


def list_user_notifications(
    db: Session,
    user_id: UUID,
    *,
    page: int = 1,
    page_size: int = 20,
    unread_only: bool = False,
) -> tuple[list[tuple[UserNotification, Notification]], int]:
    query = (
        db.query(UserNotification, Notification)
        .join(Notification, Notification.id == UserNotification.notification_id)
        .filter(UserNotification.user_id == user_id)
    )
    if unread_only:
        query = query.filter(UserNotification.is_read.is_(False))
    query = query.order_by(Notification.created_at.desc())
    total = query.count()
    rows = apply_pagination(query, page, page_size).all()
    logger.info(
        "[NOTIF_DEBUG] list_user_notifications user_id=%s page=%s page_size=%s total_matched=%s",
        user_id, page, page_size, total,
    )
    return rows, total


def get_user_notification(
    db: Session,
    user_id: UUID,
    user_notification_id: UUID,
) -> tuple[UserNotification, Notification] | None:
    return (
        db.query(UserNotification, Notification)
        .join(Notification, Notification.id == UserNotification.notification_id)
        .filter(
            UserNotification.id == user_notification_id,
            UserNotification.user_id == user_id,
        )
        .first()
    )


def mark_user_notification_read(db: Session, row: UserNotification) -> UserNotification:
    row.is_read = True
    row.read_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


def mark_all_user_notifications_read(db: Session, user_id: UUID) -> int:
    now = datetime.utcnow()
    updated = (
        db.query(UserNotification)
        .filter(UserNotification.user_id == user_id, UserNotification.is_read.is_(False))
        .update({"is_read": True, "read_at": now}, synchronize_session=False)
    )
    db.commit()
    return int(updated or 0)


def count_unread_user_notifications(db: Session, user_id: UUID) -> int:
    return (
        db.query(UserNotification)
        .filter(UserNotification.user_id == user_id, UserNotification.is_read.is_(False))
        .count()
    )


def create_notification_log(
    db: Session,
    *,
    notification_id: UUID,
    recipient_id: UUID,
    channel: str,
    status: str,
    error_message: str | None = None,
) -> NotificationLog:
    row = NotificationLog(
        notification_id=notification_id,
        recipient_id=recipient_id,
        channel=channel,
        status=status,
        error_message=error_message,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def create_template(db: Session, **fields) -> NotificationTemplate:
    row = NotificationTemplate(**fields)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_template_by_id(db: Session, template_id: UUID) -> NotificationTemplate | None:
    return db.query(NotificationTemplate).filter(NotificationTemplate.id == template_id).first()


def list_templates(
    db: Session,
    *,
    tenant_id: UUID | None = None,
    active_only: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[NotificationTemplate], int]:
    query = db.query(NotificationTemplate)
    if tenant_id is not None:
        query = query.filter(NotificationTemplate.tenant_id == tenant_id)
    if active_only:
        query = query.filter(NotificationTemplate.is_active.is_(True))
    query = query.order_by(NotificationTemplate.created_at.desc())
    total = query.count()
    rows = apply_pagination(query, page, page_size).all()
    return rows, total


def update_template(db: Session, row: NotificationTemplate, updates: dict) -> NotificationTemplate:
    for key, value in updates.items():
        if value is not None:
            setattr(row, key, value)
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


def delete_template(db: Session, row: NotificationTemplate) -> None:
    db.delete(row)
    db.commit()


def list_due_scheduled_notifications(db: Session, *, limit: int = 100) -> list[Notification]:
    now = datetime.utcnow()
    return (
        db.query(Notification)
        .filter(
            Notification.status == "scheduled",
            Notification.scheduled_at.isnot(None),
            Notification.scheduled_at <= now,
        )
        .order_by(Notification.scheduled_at.asc())
        .limit(limit)
        .all()
    )
