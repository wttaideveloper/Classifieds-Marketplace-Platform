from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.repository import chat_repo as chat_repo
from app.repository.query_utils import build_pagination_meta
from app.schemas.chat_schema import (
    NotificationPaginatedResponse,
    NotificationPreferenceResponse,
    NotificationPreferenceUpdate,
    UnreadCountResponse,
)


def _parse_user_id(current_user: dict) -> UUID:
    return UUID(str(current_user["id"]))


def register_device_service(db: Session, current_user: dict, token: str, platform: str):
    user_id = _parse_user_id(current_user)
    device = chat_repo.register_device_token(db, user_id, token, platform)
    return {
        "id": device.id,
        "token": device.token,
        "platform": device.platform,
        "is_active": device.is_active,
        "created_at": device.created_at,
    }


def remove_device_service(db: Session, current_user: dict, token: str):
    user_id = _parse_user_id(current_user)
    removed = chat_repo.deactivate_device_token(db, user_id, token)
    if not removed:
        raise HTTPException(status_code=404, detail="Device token not found")
    return {"message": "Device token removed successfully"}


def get_preferences_service(db: Session, current_user: dict):
    user_id = _parse_user_id(current_user)
    prefs = chat_repo.get_notification_preferences(db, user_id)
    return NotificationPreferenceResponse.model_validate(prefs)


def update_preferences_service(
    db: Session,
    current_user: dict,
    data: NotificationPreferenceUpdate,
):
    user_id = _parse_user_id(current_user)
    updates = data.model_dump(exclude_unset=True)
    prefs = chat_repo.update_notification_preferences(db, user_id, updates)
    return NotificationPreferenceResponse.model_validate(prefs)


def unread_count_service(db: Session, current_user: dict):
    user_id = _parse_user_id(current_user)
    unread_messages = chat_repo.count_total_unread_messages(db, user_id)
    unread_notifications = chat_repo.count_unread_notifications(db, user_id)
    return UnreadCountResponse(
        unread_messages=unread_messages,
        unread_notifications=unread_notifications,
        total_unread=unread_messages + unread_notifications,
    )


def notification_history_service(
    db: Session,
    current_user: dict,
    *,
    page: int = 1,
    page_size: int = 20,
):
    user_id = _parse_user_id(current_user)
    items, total = chat_repo.get_notification_history(
        db, user_id, page=page, page_size=page_size
    )
    from app.schemas.chat_schema import NotificationResponse

    return NotificationPaginatedResponse(
        items=[NotificationResponse.model_validate(item) for item in items],
        pagination=build_pagination_meta(total, page, page_size),
    )
