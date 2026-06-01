from sqlalchemy.orm import Session

from app.models.audit_model import AuditLog


def get_all_audit_logs_repo(
    db: Session
):
    return (
        db.query(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .all()
    )