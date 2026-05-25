from math import ceil
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.notification_model import Notification, NotificationHistory


def create_notification(db: Session, notification: Notification) -> Notification:
    db.add(notification)
    db.commit()
    db.refresh(notification)
    return notification


def create_notification_history(db: Session, history: NotificationHistory) -> NotificationHistory:
    db.add(history)
    db.commit()
    db.refresh(history)
    return history


def get_notification_by_id(db: Session, notification_id: UUID) -> Notification | None:
    return (
        db.query(Notification)
        .filter(
            Notification.id == notification_id,
            Notification.is_deleted == False,  # noqa: E712
        )
        .first()
    )


def list_notifications(
    db: Session,
    user_id: UUID,
    user_role: str,
    page: int,
    size: int,
    is_read: bool | None = None,
    notification_type: str | None = None,
):
    query = db.query(Notification).filter(
        Notification.user_id == user_id,
        Notification.user_role == user_role,
        Notification.is_deleted == False,  # noqa: E712
    )
    if is_read is not None:
        query = query.filter(Notification.is_read == is_read)
    if notification_type:
        query = query.filter(Notification.notification_type == notification_type)

    query = query.order_by(Notification.created_at.desc())
    total = query.count()
    unread_count = (
        db.query(Notification)
        .filter(
            Notification.user_id == user_id,
            Notification.user_role == user_role,
            Notification.is_deleted == False,  # noqa: E712
            Notification.is_read == False,  # noqa: E712
        )
        .count()
    )
    skip = (page - 1) * size
    rows = query.offset(skip).limit(size).all()
    return total, unread_count, rows, ceil(total / size) if size else 0


def update_notification(db: Session, notification: Notification) -> Notification:
    db.commit()
    db.refresh(notification)
    return notification
