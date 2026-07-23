from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.notification_schema import (
    NotificationCreate,
    NotificationPaginatedResponse,
    NotificationResponse,
    NotificationUpdate,
    ScheduleNotificationRequest,
    SendNotificationRequest,
    SendNotificationResult,
    SendToGroupsRequest,
    SendToTenantRequest,
    SendToUsersRequest,
)
from app.services.notification_service import (
    create_notification_service,
    delete_notification_service,
    get_notification_service,
    list_notifications_service,
    schedule_notification_service,
    send_notification_service,
    send_to_groups_service,
    send_to_tenant_service,
    send_to_users_service,
    update_notification_service,
)

router = APIRouter(tags=["Platform Notifications"])


@router.post(
    "",
    response_model=NotificationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Notification",
)
def create_notification(
    payload: NotificationCreate = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return create_notification_service(db, current_user, payload)


@router.get(
    "",
    response_model=NotificationPaginatedResponse,
    summary="List Notifications",
)
def list_notifications(
    tenant_id: UUID | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    notification_type: str | None = Query(None),
    category: str | None = Query(None),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return list_notifications_service(
        db,
        current_user,
        tenant_id=tenant_id,
        status=status_filter,
        notification_type=notification_type,
        category=category,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{notification_id}",
    response_model=NotificationResponse,
    summary="Get Notification",
)
def get_notification(
    notification_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return get_notification_service(db, current_user, notification_id)


@router.put(
    "/{notification_id}",
    response_model=NotificationResponse,
    summary="Update Notification",
)
def update_notification(
    notification_id: UUID = Path(...),
    payload: NotificationUpdate = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return update_notification_service(db, current_user, notification_id, payload)


@router.delete(
    "/{notification_id}",
    summary="Delete Notification",
)
def delete_notification(
    notification_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return delete_notification_service(db, current_user, notification_id)


@router.post(
    "/send",
    response_model=SendNotificationResult,
    summary="Send Notification Immediately",
)
def send_notification(
    payload: SendNotificationRequest = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return send_notification_service(db, current_user, payload)


@router.post(
    "/schedule",
    response_model=SendNotificationResult,
    summary="Schedule Notification",
)
def schedule_notification(
    payload: ScheduleNotificationRequest = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return schedule_notification_service(db, current_user, payload)


@router.post(
    "/send-to-tenant",
    response_model=SendNotificationResult,
    summary="Send Notification To Tenant",
)
def send_to_tenant(
    payload: SendToTenantRequest = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return send_to_tenant_service(db, current_user, payload)


@router.post(
    "/send-to-users",
    response_model=SendNotificationResult,
    summary="Send Notification To Selected Users",
)
def send_to_users(
    payload: SendToUsersRequest = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return send_to_users_service(db, current_user, payload)


@router.post(
    "/send-to-groups",
    response_model=SendNotificationResult,
    summary="Send Notification To User Group",
)
def send_to_groups(
    payload: SendToGroupsRequest = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return send_to_groups_service(db, current_user, payload)
