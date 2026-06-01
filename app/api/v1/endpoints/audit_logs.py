from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from uuid import UUID
from app.db.database import get_db
from app.schemas.audit_schema import (
    AuditExportRequest,
    AuditExportListResponse,
    AuditLogListResponse,
    UserAuthenticationLogsResponse,
    EntityAuditLogListResponse
)
from app.services.audit_service import (
    export_audit_logs_service,
    get_all_audit_logs_service,
    get_user_authentication_logs_service,
    get_entity_audit_logs_service
)

router = APIRouter(
    tags=["Audit"]
)

@router.post(
    "/export",
    response_model=AuditExportListResponse,
    status_code=status.HTTP_200_OK
)
def export_audit_logs(
    payload: AuditExportRequest,
    db: Session = Depends(get_db)
):
    return export_audit_logs_service(
        payload,
        db
    )

@router.get(
    "/logs",
    response_model=AuditLogListResponse
)
def get_all_audit_logs(
    db: Session = Depends(get_db)
):
    return get_all_audit_logs_service(db)

@router.get(
    "/user/{user_id}",
    response_model=UserAuthenticationLogsResponse,
    status_code=status.HTTP_200_OK
)
def get_user_authentication_logs(
    user_id: UUID,
    db: Session = Depends(get_db)
):
    return get_user_authentication_logs_service(
        user_id=user_id,
        db=db
    )

@router.get(
    "/entity/{entity_id}",
    response_model=EntityAuditLogListResponse,
    status_code=status.HTTP_200_OK
)
def get_entity_audit_logs(
    entity_id: UUID,
    db: Session = Depends(get_db)
):
    return get_entity_audit_logs_service(
        entity_id=entity_id,
        db=db
    )