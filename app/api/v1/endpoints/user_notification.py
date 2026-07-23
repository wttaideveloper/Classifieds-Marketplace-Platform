from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.notification_schema import (
    MarkAllReadResponse,
    MarkReadResponse,
    UnreadNotificationCountResponse,
    UserNotificationPaginatedResponse,
)
from app.services.notification_service import (
    list_my_notifications_service,
    mark_all_my_notifications_read_service,
    mark_my_notification_read_service,
    my_unread_count_service,
)

router = APIRouter(tags=["User Notifications"])


@router.get(
    "/me/notifications",
    response_model=UserNotificationPaginatedResponse,
    summary="Get My Notifications",
)
def get_my_notifications(
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return list_my_notifications_service(db, current_user, page=page, page_size=page_size)


@router.put(
    "/me/notifications/{notification_id}/read",
    response_model=MarkReadResponse,
    summary="Mark My Notification Read",
)
def mark_my_notification_read(
    notification_id: UUID = Path(..., description="User notification row id."),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return mark_my_notification_read_service(db, current_user, notification_id)


@router.put(
    "/me/notifications/read-all",
    response_model=MarkAllReadResponse,
    summary="Mark All My Notifications Read",
)
def mark_all_my_notifications_read(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return mark_all_my_notifications_read_service(db, current_user)


@router.get(
    "/me/notifications/unread-count",
    response_model=UnreadNotificationCountResponse,
    summary="Get My Platform Notification Unread Count",
)
def get_my_unread_count(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return my_unread_count_service(db, current_user)
