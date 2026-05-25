from math import ceil
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.push_notification_model import DeviceToken, PushNotification


def upsert_device_token(
    db: Session,
    *,
    user_id: UUID,
    device_token: str,
    device_type: str,
) -> DeviceToken:
    token = (
        db.query(DeviceToken)
        .filter(
            DeviceToken.user_id == user_id,
            DeviceToken.device_token == device_token,
        )
        .first()
    )
    if token:
        token.device_type = device_type
        token.is_active = True
    else:
        token = DeviceToken(
            user_id=user_id,
            device_token=device_token,
            device_type=device_type,
            is_active=True,
        )
        db.add(token)
    db.commit()
    db.refresh(token)
    return token


def get_device_token_by_id(db: Session, device_id: UUID) -> DeviceToken | None:
    return db.query(DeviceToken).filter(DeviceToken.id == device_id).first()


def deactivate_device_token(db: Session, token: DeviceToken) -> DeviceToken:
    token.is_active = False
    db.commit()
    db.refresh(token)
    return token


def list_active_device_tokens(db: Session, user_id: UUID) -> list[DeviceToken]:
    return (
        db.query(DeviceToken)
        .filter(DeviceToken.user_id == user_id, DeviceToken.is_active == True)  # noqa: E712
        .all()
    )


def create_push_notification(db: Session, notification: PushNotification) -> PushNotification:
    db.add(notification)
    db.commit()
    db.refresh(notification)
    return notification


def get_push_notification_by_id(
    db: Session, notification_id: UUID
) -> PushNotification | None:
    return db.query(PushNotification).filter(PushNotification.id == notification_id).first()


def update_push_notification(db: Session, notification: PushNotification) -> PushNotification:
    db.commit()
    db.refresh(notification)
    return notification


def list_push_notifications(db: Session, user_id: UUID, page: int, size: int):
    query = (
        db.query(PushNotification)
        .filter(PushNotification.user_id == user_id)
        .order_by(PushNotification.created_at.desc())
    )
    total = query.count()
    unread_count = (
        db.query(PushNotification)
        .filter(
            PushNotification.user_id == user_id,
            PushNotification.is_read == False,  # noqa: E712
        )
        .count()
    )
    rows = query.offset((page - 1) * size).limit(size).all()
    return total, unread_count, rows, ceil(total / size) if size else 0
