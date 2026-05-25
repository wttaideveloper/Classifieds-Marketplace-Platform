from uuid import UUID

from fastapi import status
from sqlalchemy.orm import Session

from app.exceptions.custom_exception import CustomException
from app.models.notification_model import (
    Notification,
    NotificationHistory,
    NotificationHistoryAction,
    NotificationType,
    UserRole,
)
from app.repository.admin_repo import get_admin_by_id
from app.repository.customer_repo import get_customer_by_id
from app.repository.merchant_repo import get_merchant_by_id
from app.repository.notification_repo import (
    create_notification,
    create_notification_history,
    get_notification_by_id,
    list_notifications,
    update_notification,
)
from app.schemas.notification_schema import CreateNotificationSchema


def _parse_uuid(value: str, field_name: str) -> UUID:
    try:
        return UUID(str(value))
    except Exception:
        raise CustomException(400, f"Invalid {field_name}")


def _admin_user_uuid(admin_id: int) -> UUID:
    return UUID(int=admin_id)


def _validate_user_exists(db: Session, user_id: UUID, user_role: str) -> None:
    role = user_role.lower()
    if role == UserRole.customer.value:
        if not get_customer_by_id(db, str(user_id)):
            raise CustomException(404, "Customer not found")
    elif role == UserRole.merchant.value:
        if not get_merchant_by_id(db, str(user_id)):
            raise CustomException(404, "Merchant not found")
    elif role == UserRole.admin.value:
        if not get_admin_by_id(db, user_id.int):
            raise CustomException(404, "Admin not found")
    else:
        raise CustomException(400, "Invalid user_role")


def _normalize_user_id(user_id: UUID, user_role: str) -> UUID:
    if user_role.lower() == UserRole.admin.value:
        return _admin_user_uuid(user_id.int)
    return user_id


def _notification_to_dict(notification: Notification) -> dict:
    return {
        "notification_id": str(notification.id),
        "title": notification.title,
        "message": notification.message,
        "notification_type": notification.notification_type,
        "reference_id": str(notification.reference_id) if notification.reference_id else None,
        "is_read": notification.is_read,
        "created_at": notification.created_at,
    }


def _record_history(db: Session, notification_id: UUID, action: str) -> None:
    create_notification_history(
        db,
        NotificationHistory(notification_id=notification_id, action=action),
    )


def _current_user_key(current_user: dict) -> tuple[str, str]:
    role = (current_user.get("role") or "").lower()
    raw_id = current_user.get("id")
    if role == UserRole.admin.value:
        return role, str(_admin_user_uuid(int(raw_id)))
    return role, str(_parse_uuid(raw_id, "user_id"))


def _assert_owner(notification: Notification, current_user: dict) -> None:
    role, user_id = _current_user_key(current_user)
    if role == UserRole.admin.value:
        return
    if role != notification.user_role or str(notification.user_id) != user_id:
        raise CustomException(403, "Not authorized to access this notification")


def _can_create_for_user(current_user: dict, target_user_id: UUID, target_role: str) -> None:
    role, user_id = _current_user_key(current_user)
    if role == UserRole.admin.value:
        return
    normalized_target = _normalize_user_id(target_user_id, target_role)
    if role != target_role.lower() or user_id != str(normalized_target):
        raise CustomException(403, "Not authorized to create notification for this user")


def create_notification_record(
    db: Session,
    *,
    user_id: UUID,
    user_role: str,
    title: str,
    message: str,
    notification_type: str,
    reference_id: UUID | None = None,
) -> Notification:
    normalized_role = user_role.lower()
    normalized_user_id = _normalize_user_id(user_id, normalized_role)
    _validate_user_exists(db, normalized_user_id, normalized_role)

    notification = Notification(
        user_id=normalized_user_id,
        user_role=normalized_role,
        title=title,
        message=message,
        notification_type=notification_type,
        reference_id=reference_id,
        is_read=False,
        is_deleted=False,
    )
    notification = create_notification(db, notification)
    _record_history(db, notification.id, NotificationHistoryAction.Sent.value)
    return notification


def create_notification_service(db: Session, payload: CreateNotificationSchema, current_user: dict):
    try:
        target_role = payload.user_role.value
        _can_create_for_user(current_user, payload.user_id, target_role)
        notification = create_notification_record(
            db,
            user_id=payload.user_id,
            user_role=target_role,
            title=payload.title,
            message=payload.message,
            notification_type=payload.notification_type.value,
            reference_id=payload.reference_id,
        )
        return {
            "success": True,
            "message": "Notification created successfully",
            "data": _notification_to_dict(notification),
        }
    except CustomException:
        raise
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def list_notifications_service(
    db: Session,
    current_user: dict,
    page: int,
    size: int,
    is_read: bool | None = None,
    notification_type: str | None = None,
):
    try:
        role = (current_user.get("role") or "").lower()
        if role not in {UserRole.customer.value, UserRole.merchant.value, UserRole.admin.value}:
            raise CustomException(403, "Not authorized")

        if role == UserRole.admin.value:
            user_id = _admin_user_uuid(int(current_user.get("id")))
        else:
            user_id = _normalize_user_id(_parse_uuid(current_user.get("id"), "user_id"), role)

        total, unread_count, rows, total_pages = list_notifications(
            db,
            user_id=user_id,
            user_role=role,
            page=page,
            size=size,
            is_read=is_read,
            notification_type=notification_type,
        )
        return {
            "success": True,
            "page": page,
            "size": size,
            "total_elements": total,
            "total_pages": total_pages,
            "unread_count": unread_count,
            "data": [_notification_to_dict(n) for n in rows],
        }
    except CustomException:
        raise
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def get_notification_service(db: Session, notification_id: str, current_user: dict):
    try:
        nid = _parse_uuid(notification_id, "notification_id")
        notification = get_notification_by_id(db, nid)
        if not notification:
            raise CustomException(404, "Notification not found")
        _assert_owner(notification, current_user)
        return {"success": True, "data": _notification_to_dict(notification)}
    except CustomException:
        raise
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def mark_notification_read_service(db: Session, notification_id: str, current_user: dict):
    try:
        nid = _parse_uuid(notification_id, "notification_id")
        notification = get_notification_by_id(db, nid)
        if not notification:
            raise CustomException(404, "Notification not found")
        _assert_owner(notification, current_user)
        if not notification.is_read:
            notification.is_read = True
            update_notification(db, notification)
            _record_history(db, notification.id, NotificationHistoryAction.Read.value)
        return {
            "success": True,
            "message": "Notification marked as read",
            "data": _notification_to_dict(notification),
        }
    except CustomException:
        raise
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def delete_notification_service(db: Session, notification_id: str, current_user: dict):
    try:
        nid = _parse_uuid(notification_id, "notification_id")
        notification = get_notification_by_id(db, nid)
        if not notification:
            raise CustomException(404, "Notification not found")
        _assert_owner(notification, current_user)
        notification.is_deleted = True
        update_notification(db, notification)
        _record_history(db, notification.id, NotificationHistoryAction.Deleted.value)
        return {"success": True, "message": "Notification deleted"}
    except CustomException:
        raise
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def notify_booking_created(db: Session, booking) -> None:
    booking_id = booking.id if hasattr(booking, "id") else booking.get("booking_id")
    booking_number = (
        booking.booking_number
        if hasattr(booking, "booking_number")
        else booking.get("booking_number", "")
    )
    customer_id = booking.customer_id if hasattr(booking, "customer_id") else None
    merchant_id = booking.merchant_id if hasattr(booking, "merchant_id") else None
    ref = UUID(str(booking_id)) if booking_id else None

    if customer_id:
        create_notification_record(
            db,
            user_id=customer_id,
            user_role=UserRole.customer.value,
            title="Booking received",
            message=f"Booking {booking_number} is pending confirmation.",
            notification_type=NotificationType.Booking.value,
            reference_id=ref,
        )
    if merchant_id:
        create_notification_record(
            db,
            user_id=merchant_id,
            user_role=UserRole.merchant.value,
            title="New booking",
            message=f"New booking {booking_number} requires your attention.",
            notification_type=NotificationType.Booking.value,
            reference_id=ref,
        )


def notify_order_created(db: Session, order) -> None:
    create_notification_record(
        db,
        user_id=order.customer_id,
        user_role=UserRole.customer.value,
        title="Order placed",
        message=f"Order {order.order_number} was placed successfully.",
        notification_type=NotificationType.Order.value,
        reference_id=order.id,
    )
    if order.merchant_id:
        create_notification_record(
            db,
            user_id=order.merchant_id,
            user_role=UserRole.merchant.value,
            title="New order",
            message=f"You received order {order.order_number}.",
            notification_type=NotificationType.Order.value,
            reference_id=order.id,
        )


def notify_order_status_updated(db: Session, order, new_status: str) -> None:
    create_notification_record(
        db,
        user_id=order.customer_id,
        user_role=UserRole.customer.value,
        title="Order status updated",
        message=f"Order {order.order_number} is now {new_status}.",
        notification_type=NotificationType.Order.value,
        reference_id=order.id,
    )
