from fastapi import status
from app.exceptions.custom_exception import CustomException
from app.models.audit_model import AuditLog
from app.schemas.audit_schema import (
    AuditExportRequest,
    AuditExportResponse,
    AuditExportListResponse,
    AuditLogResponse,
    AuditLogListResponse,
    UserAuthenticationLogsResponse,
    AuthenticationLogResponse,
    EntityAuditLogListResponse
)
from uuid import UUID
from sqlalchemy.orm import Session
from app.repository.audit_repo import (
    get_all_audit_logs_repo,
    get_user_authentication_logs_repo,
    get_entity_audit_logs_repo
)

def export_audit_logs_service(
    payload,
    db
):
    try:
        query = db.query(AuditLog)

        if payload.start_date:
            query = query.filter(
                AuditLog.created_at >= payload.start_date
            )

        if payload.end_date:
            query = query.filter(
                AuditLog.created_at <= payload.end_date
            )

        if payload.module_name:
            query = query.filter(
                AuditLog.module_name.ilike(
                    f"%{payload.module_name}%"
                )
            )

        if payload.user_id:
            query = query.filter(
                AuditLog.user_id == payload.user_id
            )

        logs = query.order_by(
            AuditLog.created_at.desc()
        ).all()

        return AuditExportListResponse(
            total_records=len(logs),
            records=[
                AuditExportResponse(
                    audit_log_id=log.id,
                    action_type=log.action_type,
                    module_name=log.module_name,
                    action_status=log.action_status.value,
                    created_at=log.created_at
                )
                for log in logs
            ]
        )

    except Exception as e:
        raise CustomException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to export audit logs: {str(e)}"
        )

def get_all_audit_logs_service(
    db: Session
):
    try:
        audit_logs = get_all_audit_logs_repo(db)

        if not audit_logs:
            raise CustomException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No audit logs found"
            )

        return AuditLogListResponse(
            total_records=len(audit_logs),
            records=[
                AuditLogResponse.model_validate(log)
                for log in audit_logs
            ]
        )

    except CustomException:
        raise

    except Exception as e:
        raise CustomException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch audit logs: {str(e)}"
        )


def get_user_authentication_logs_service(
    user_id: UUID,
    db: Session
):
    try:
        logs = get_user_authentication_logs_repo(
            db=db,
            user_id=user_id
        )

        if not logs:
            raise CustomException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Authentication logs not found for this user"
            )

        return UserAuthenticationLogsResponse(
            total_records=len(logs),
            records=[
                AuthenticationLogResponse.model_validate(log)
                for log in logs
            ]
        )

    except CustomException:
        raise

    except Exception as e:
        raise CustomException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch authentication logs: {str(e)}"
        )

def get_entity_audit_logs_service(
    entity_id: UUID,
    db: Session
):
    try:
        logs = get_entity_audit_logs_repo(
            db=db,
            entity_id=entity_id
        )

        if not logs:
            raise CustomException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Audit logs not found for this entity"
            )

        return EntityAuditLogListResponse(
            total_records=len(logs),
            records=[
                AuditLogResponse.model_validate(log)
                for log in logs
            ]
        )

    except CustomException:
        raise

    except Exception as e:
        raise CustomException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch audit logs: {str(e)}"
        )