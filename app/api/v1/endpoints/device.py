from fastapi import APIRouter, Body, Depends, Path, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.schemas.chat_schema import DeviceRegisterRequest, DeviceTokenResponse
from app.services.chat_notification_service import register_device_service, remove_device_service

router = APIRouter(tags=["Devices"])


@router.post(
    "/register",
    response_model=DeviceTokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register Device Token",
    description=(
        "Register a Firebase Cloud Messaging (FCM) device token for push notifications. "
        "Mobile apps should pass the FCM token from Firebase SDK."
    ),
)
def register_device(
    payload: DeviceRegisterRequest = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return register_device_service(db, current_user, payload.token, payload.platform)


@router.delete(
    "/{token}",
    summary="Remove Device Token",
    description="Deactivate an FCM device token so Firebase push is no longer sent to that device.",
)
def remove_device(
    token: str = Path(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return remove_device_service(db, current_user, token)
