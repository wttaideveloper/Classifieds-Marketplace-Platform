from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.schemas.push_notification_schema import (
    DeleteDeviceResponse,
    PushNotificationListResponse,
    PushNotificationReadResponse,
    RegisterDeviceRequest,
    RegisterDeviceResponse,
    SendPushNotificationRequest,
    SendPushNotificationResponse,
)
from app.services.push_notification_service import (
    delete_device_service,
    list_push_notifications_service,
    mark_push_notification_read_service,
    register_device_service,
    send_push_notification_service,
)

router = APIRouter(tags=["Push Notifications"])


@router.post("/register-device", status_code=201, response_model=RegisterDeviceResponse)
def register_device(
    payload: RegisterDeviceRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return register_device_service(db=db, payload=payload, current_user=current_user)


@router.post("/send", status_code=201, response_model=SendPushNotificationResponse)
def send_push_notification(
    payload: SendPushNotificationRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return send_push_notification_service(db=db, payload=payload, current_user=current_user)


@router.get("/notifications", status_code=200, response_model=PushNotificationListResponse)
def list_push_notifications(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return list_push_notifications_service(
        db=db, current_user=current_user, page=page, size=size
    )


@router.put(
    "/notifications/{id}/read",
    status_code=200,
    response_model=PushNotificationReadResponse,
)
def mark_push_notification_read(
    id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return mark_push_notification_read_service(
        db=db, notification_id=id, current_user=current_user
    )


@router.delete("/device/{id}", status_code=200, response_model=DeleteDeviceResponse)
def delete_device(
    id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return delete_device_service(db=db, device_id=id, current_user=current_user)
