from datetime import datetime, timezone
from uuid import UUID

from fastapi import status
from sqlalchemy.orm import Session

from app.exceptions.custom_exception import CustomException
from app.models.push_notification_model import DeliveryStatus, PushNotification
from app.repository.push_notification_repo import (
    create_push_notification,
    deactivate_device_token,
    get_device_token_by_id,
    get_push_notification_by_id,
    list_active_device_tokens,
    list_push_notifications,
    update_push_notification,
    upsert_device_token,
)
from app.schemas.push_notification_schema import (
    RegisterDeviceRequest,
    SendPushNotificationRequest,
)
from app.services.push_provider_service import get_push_provider


def _parse_uuid(value: str, field_name: str) -> UUID:
    try:
        return UUID(str(value))
    except Exception:
        raise CustomException(400, f"Invalid {field_name}")


def _current_user_id(current_user: dict) -> UUID:
    role = (current_user.get("role") or "").lower()
    raw_id = current_user.get("id")
    if role == "admin":
        return UUID(int=int(raw_id))
    return _parse_uuid(raw_id, "user_id")


def _assert_can_access_user(current_user: dict, user_id: UUID) -> None:
    role = (current_user.get("role") or "").lower()
    if role == "admin":
        return
    if _current_user_id(current_user) != user_id:
        raise CustomException(403, "Not authorized")


def _notification_to_dict(notification: PushNotification) -> dict:
    return {
        "notification_id": notification.id,
        "user_id": notification.user_id,
        "title": notification.title,
        "message": notification.message,
        "notification_type": notification.notification_type,
        "reference_id": notification.reference_id,
        "delivery_status": notification.delivery_status,
        "is_read": notification.is_read,
        "sent_at": notification.sent_at,
        "created_at": notification.created_at,
    }


def _send_to_tokens(tokens: list, payload) -> tuple[bool, datetime | None, str]:
    provider = get_push_provider()
    result = provider.send_to_tokens(
        tokens=[token.device_token for token in tokens],
        title=payload.title,
        message=payload.message,
        data={
            "notification_type": payload.notification_type.value,
            "reference_id": str(payload.reference_id) if payload.reference_id else "",
        },
    )
    sent_at = datetime.now(timezone.utc) if result.success else None
    if result.success:
        details = (
            f"Push notification delivered via {result.provider} "
            f"to {result.delivered_count} device(s)"
        )
    else:
        details = result.error or "Push notification delivery failed"
    return result.success, sent_at, details


def register_device_service(
    db: Session, payload: RegisterDeviceRequest, current_user: dict
) -> dict:
    try:
        token = upsert_device_token(
            db,
            user_id=_current_user_id(current_user),
            device_token=payload.device_token,
            device_type=payload.device_type.value,
        )
        return {
            "success": True,
            "message": "Device registered successfully",
            "data": token,
        }
    except CustomException:
        raise
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def send_push_notification_service(
    db: Session, payload: SendPushNotificationRequest, current_user: dict
) -> dict:
    try:
        _assert_can_access_user(current_user, payload.user_id)
        active_tokens = list_active_device_tokens(db, payload.user_id)
        delivered, sent_at, delivery_message = _send_to_tokens(active_tokens, payload)
        delivery_status = DeliveryStatus.Sent.value if delivered else DeliveryStatus.Failed.value

        notification = create_push_notification(
            db,
            PushNotification(
                user_id=payload.user_id,
                title=payload.title,
                message=payload.message,
                notification_type=payload.notification_type.value,
                reference_id=payload.reference_id,
                delivery_status=delivery_status,
                is_read=False,
                sent_at=sent_at,
            ),
        )
        return {
            "success": delivered,
            "message": delivery_message,
            "data": {
                "notification_id": notification.id,
                "delivery_status": notification.delivery_status,
                "sent_at": notification.sent_at,
            },
        }
    except CustomException:
        raise
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def list_push_notifications_service(
    db: Session, current_user: dict, page: int, size: int
) -> dict:
    try:
        user_id = _current_user_id(current_user)
        total, unread_count, rows, total_pages = list_push_notifications(
            db, user_id=user_id, page=page, size=size
        )
        return {
            "success": True,
            "page": page,
            "size": size,
            "total_elements": total,
            "total_pages": total_pages,
            "unread_count": unread_count,
            "data": [_notification_to_dict(row) for row in rows],
        }
    except CustomException:
        raise
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def mark_push_notification_read_service(
    db: Session, notification_id: str, current_user: dict
) -> dict:
    try:
        notification = get_push_notification_by_id(
            db, _parse_uuid(notification_id, "notification_id")
        )
        if not notification:
            raise CustomException(404, "Push notification not found")
        _assert_can_access_user(current_user, notification.user_id)
        if not notification.is_read:
            notification.is_read = True
            notification = update_push_notification(db, notification)
        return {
            "success": True,
            "message": "Push notification marked as read",
            "data": _notification_to_dict(notification),
        }
    except CustomException:
        raise
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def delete_device_service(db: Session, device_id: str, current_user: dict) -> dict:
    try:
        token = get_device_token_by_id(db, _parse_uuid(device_id, "device_id"))
        if not token:
            raise CustomException(404, "Device token not found")
        _assert_can_access_user(current_user, token.user_id)
        deactivate_device_token(db, token)
        return {"success": True, "message": "Device token deleted"}
    except CustomException:
        raise
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))
