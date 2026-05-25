from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import SessionLocal
from app.schemas.notification_schema import (
    CreateNotificationSchema,
    NotificationCreateResponseSchema,
    PaginatedNotificationsResponse,
)
from app.services.notification_service import (
    create_notification_service,
    delete_notification_service,
    get_notification_service,
    list_notifications_service,
    mark_notification_read_service,
)

router = APIRouter(tags=["Notifications"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("", status_code=201, response_model=NotificationCreateResponseSchema)
def create_notification(
    payload: CreateNotificationSchema,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return create_notification_service(db=db, payload=payload, current_user=current_user)


@router.get("", status_code=200, response_model=PaginatedNotificationsResponse)
def list_notifications(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=50),
    is_read: Optional[bool] = Query(None),
    notification_type: Optional[str] = Query(
        None, description="Booking | Order | Review | System | Payment"
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return list_notifications_service(
        db=db,
        current_user=current_user,
        page=page,
        size=size,
        is_read=is_read,
        notification_type=notification_type,
    )


@router.get("/{notification_id}", status_code=200)
def get_notification(
    notification_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return get_notification_service(
        db=db, notification_id=notification_id, current_user=current_user
    )


@router.put("/{notification_id}/read", status_code=200)
def mark_notification_read(
    notification_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return mark_notification_read_service(
        db=db, notification_id=notification_id, current_user=current_user
    )


@router.delete("/{notification_id}", status_code=200)
def delete_notification(
    notification_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return delete_notification_service(
        db=db, notification_id=notification_id, current_user=current_user
    )
