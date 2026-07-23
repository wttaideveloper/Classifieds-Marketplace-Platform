from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.notification_schema import (
    NotificationTemplateCreate,
    NotificationTemplatePaginatedResponse,
    NotificationTemplateResponse,
    NotificationTemplateUpdate,
)
from app.services.notification_service import (
    create_template_service,
    delete_template_service,
    list_templates_service,
    update_template_service,
)

router = APIRouter(tags=["Notification Templates"])


@router.post(
    "",
    response_model=NotificationTemplateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Notification Template",
)
def create_template(
    payload: NotificationTemplateCreate = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return create_template_service(db, current_user, payload)


@router.get(
    "",
    response_model=NotificationTemplatePaginatedResponse,
    summary="List Notification Templates",
)
def list_templates(
    tenant_id: UUID | None = Query(None),
    active_only: bool = Query(False),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return list_templates_service(
        db,
        current_user,
        tenant_id=tenant_id,
        active_only=active_only,
        page=page,
        page_size=page_size,
    )


@router.put(
    "/{template_id}",
    response_model=NotificationTemplateResponse,
    summary="Update Notification Template",
)
def update_template(
    template_id: UUID = Path(...),
    payload: NotificationTemplateUpdate = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return update_template_service(db, current_user, template_id, payload)


@router.delete(
    "/{template_id}",
    summary="Delete Notification Template",
)
def delete_template(
    template_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return delete_template_service(db, current_user, template_id)
