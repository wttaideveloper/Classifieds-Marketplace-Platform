from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.chat_schema import (
    ChatEligibilityResponse,
    ConversationNotificationsReadResponse,
    MonthlyLimitResponse,
    NotificationPaginatedResponse,
    NotificationPreferenceResponse,
    NotificationPreferenceUpdate,
    NotificationReadAllResponse,
    NotificationResponse,
    UnreadCountResponse,
)
from app.services.chat_notification_service import (
    get_preferences_service,
    mark_all_notifications_read_service,
    mark_conversation_notifications_read_service,
    mark_notification_read_service,
    notification_history_service,
    unread_count_service,
    update_preferences_service,
)
from app.services.chat_service import (
    chat_eligibility_service,
    monthly_limit_service,
    remaining_messages_service,
)

router = APIRouter(tags=["Chat Notifications"])


@router.get(
    "/unread-count",
    response_model=UnreadCountResponse,
    summary="Get Unread Count",
    description="Returns unread message and notification counts for the authenticated user.",
)
def get_unread_count(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return unread_count_service(db, current_user)


@router.get(
    "/preferences",
    response_model=NotificationPreferenceResponse,
    summary="Get Notification Preferences",
)
def get_notification_preferences(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return get_preferences_service(db, current_user)


@router.put(
    "/preferences",
    response_model=NotificationPreferenceResponse,
    summary="Update Notification Preferences",
)
def update_notification_preferences(
    payload: NotificationPreferenceUpdate = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return update_preferences_service(db, current_user, payload)


@router.get(
    "/history",
    response_model=NotificationPaginatedResponse,
    summary="Get Notification History",
)
def get_notification_history(
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return notification_history_service(db, current_user, page=page, page_size=page_size)


@router.patch(
    "/read-all",
    response_model=NotificationReadAllResponse,
    summary="Mark All Notifications Read",
    description="Mark every unread notification as read for the authenticated user.",
)
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return mark_all_notifications_read_service(db, current_user)


@router.patch(
    "/conversation/{conversation_id}/read",
    response_model=ConversationNotificationsReadResponse,
    summary="Mark Conversation Notifications Read",
    description=(
        "Mark all unread chat notifications for a conversation as read. "
        "Also called automatically by `PATCH /conversations/{id}/read`."
    ),
)
def mark_conversation_notifications_read(
    conversation_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return mark_conversation_notifications_read_service(db, current_user, conversation_id)


@router.patch(
    "/{notification_id}/read",
    response_model=NotificationResponse,
    summary="Mark Notification Read",
    description="Mark a single notification as read. Returns the updated notification.",
)
def mark_notification_read(
    notification_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return mark_notification_read_service(db, current_user, notification_id)
