from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.health_model import AlertConfiguration, ErrorLog, SystemHealthLog


def create_health_log(db: Session, health_log: SystemHealthLog):
    try:
        db.add(health_log)
        db.commit()
        db.refresh(health_log)
        return health_log
    except Exception:
        db.rollback()
        raise


def list_latest_health_logs(db: Session, service_type: str | None = None, limit: int = 20):
    query = db.query(SystemHealthLog)
    if service_type:
        query = query.filter(SystemHealthLog.service_type == service_type)
    return query.order_by(SystemHealthLog.checked_at.desc()).limit(limit).all()


def count_recent_errors(db: Session):
    return db.query(ErrorLog).count()


def summarize_errors(db: Session):
    rows = (
        db.query(
            ErrorLog.service_name,
            ErrorLog.severity_level,
            func.count(ErrorLog.id).label("error_count"),
            func.max(ErrorLog.created_at).label("latest_at"),
        )
        .group_by(ErrorLog.service_name, ErrorLog.severity_level)
        .order_by(func.count(ErrorLog.id).desc())
        .all()
    )
    return rows


def get_latest_error_message(db: Session, service_name: str, severity_level: str):
    row = (
        db.query(ErrorLog)
        .filter(
            ErrorLog.service_name == service_name,
            ErrorLog.severity_level == severity_level,
        )
        .order_by(ErrorLog.created_at.desc())
        .first()
    )
    return row.error_message if row else None


def create_alert_config(db: Session, config: AlertConfiguration):
    try:
        db.add(config)
        db.commit()
        db.refresh(config)
        return config
    except Exception:
        db.rollback()
        raise


def list_active_alert_configs(db: Session):
    return db.query(AlertConfiguration).filter(AlertConfiguration.is_active == True).all()
