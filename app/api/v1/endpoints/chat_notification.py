from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.chat_schema import (
    ChatEligibilityResponse,
    MonthlyLimitResponse,
    NotificationPaginatedResponse,
    NotificationPreferenceResponse,
    NotificationPreferenceUpdate,
    UnreadCountResponse,
)
from app.services.chat_notification_service import (
    get_preferences_service,
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
