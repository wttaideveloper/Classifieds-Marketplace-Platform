from sqlalchemy.orm import Session
from uuid import UUID
from app.models.audit_model import AuditLog, AuthenticationLog


def get_all_audit_logs_repo(
    db: Session
):
    return (
        db.query(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .all()
    )

def get_user_authentication_logs_repo(
    db: Session,
    user_id: UUID
):
    return (
        db.query(AuthenticationLog)
        .filter(AuthenticationLog.user_id == user_id)
        .order_by(AuthenticationLog.created_at.desc())
        .all()
    )

def get_entity_audit_logs_repo(
    db: Session,
    entity_id: UUID
):
    return (
        db.query(AuditLog)
        .filter(AuditLog.entity_id == entity_id)
        .order_by(AuditLog.created_at.desc())
        .all()
    )