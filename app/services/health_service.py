import shutil
import time
from decimal import Decimal

from fastapi import status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.database import engine
from app.exceptions.custom_exception import CustomException
from app.models.health_model import AlertConfiguration, HealthStatus, ServiceType, SystemHealthLog
from app.repository.health_repo import (
    count_recent_errors,
    create_alert_config,
    create_health_log,
    get_latest_error_message,
    list_active_alert_configs,
    list_latest_health_logs,
    summarize_errors,
)


def _percent(value: float) -> Decimal:
    return Decimal(str(round(max(0.0, min(100.0, value)), 2)))


def _disk_usage_percent() -> Decimal:
    usage = shutil.disk_usage(".")
    return _percent((usage.used / usage.total) * 100)


def _system_status(cpu_usage=None, memory_usage=None, disk_usage=None, error_count: int = 0):
    values = [v for v in (cpu_usage, memory_usage, disk_usage) if v is not None]
    if error_count >= 10 or any(float(v) >= 90 for v in values):
        return HealthStatus.Critical
    if error_count >= 3 or any(float(v) >= 75 for v in values):
        return HealthStatus.Warning
    return HealthStatus.Healthy


def _api_status(response_time_ms: int, error_count: int):
    if response_time_ms >= 3000 or error_count >= 10:
        return HealthStatus.Critical
    if response_time_ms >= 1000 or error_count >= 3:
        return HealthStatus.Warning
    return HealthStatus.Healthy


def _alert_status(db: Session, metric_values: dict[str, float]):
    if db is None:
        return "Not Triggered"
    alerts = list_active_alert_configs(db)
    for alert in alerts:
        value = metric_values.get(alert.metric_type.value)
        if value is not None and value >= float(alert.threshold_value):
            return "Triggered"
    return "Not Triggered"


def _log_to_dict(log: SystemHealthLog, alert_status: str):
    return {
        "service_name": log.service_name,
        "service_type": log.service_type.value,
        "health_status": log.health_status.value,
        "response_time_ms": log.response_time_ms,
        "cpu_usage": float(log.cpu_usage) if log.cpu_usage is not None else None,
        "memory_usage": float(log.memory_usage) if log.memory_usage is not None else None,
        "disk_usage": float(log.disk_usage) if log.disk_usage is not None else None,
        "error_count": log.error_count,
        "checked_at": log.checked_at,
        "alert_status": alert_status,
    }


def get_system_health_service(db: Session):
    try:
        started = time.perf_counter()
        disk_usage = _disk_usage_percent()
        error_count = count_recent_errors(db) if db is not None else 0
        response_time_ms = int((time.perf_counter() - started) * 1000)
        health_status = _system_status(disk_usage=disk_usage, error_count=error_count)
        alert_status = _alert_status(
            db,
            {
                "CPU": 0,
                "Memory": 0,
                "Error": float(error_count),
            },
        )
        log = SystemHealthLog(
            service_name="Marketplace API Server",
            service_type=ServiceType.Server,
            health_status=health_status,
            response_time_ms=response_time_ms,
            cpu_usage=Decimal("0"),
            memory_usage=Decimal("0"),
            disk_usage=disk_usage,
            error_count=error_count,
        )
        if db is not None:
            log = create_health_log(db, log)
        return {
            "success": True,
            "message": "System health fetched successfully",
            "data": _log_to_dict(log, alert_status),
        }
    except CustomException:
        raise
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def get_database_health_service(db: Session):
    try:
        started = time.perf_counter()
        status_value = HealthStatus.Healthy
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            status_value = HealthStatus.Critical
        response_time_ms = int((time.perf_counter() - started) * 1000)
        log = SystemHealthLog(
            service_name="Primary Database",
            service_type=ServiceType.Database,
            health_status=status_value,
            response_time_ms=response_time_ms,
            error_count=0 if status_value == HealthStatus.Healthy else 1,
        )
        if db is not None:
            log = create_health_log(db, log)
        return {
            "success": True,
            "message": "Database health fetched successfully",
            "data": _log_to_dict(log, "Triggered" if status_value == HealthStatus.Critical else "Not Triggered"),
        }
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def get_api_health_service(db: Session):
    try:
        logs = list_latest_health_logs(db, service_type=ServiceType.API, limit=20) if db is not None else []
        data = [
            {
                "service_name": log.service_name,
                "health_status": log.health_status.value,
                "response_time_ms": log.response_time_ms,
                "error_count": log.error_count,
                "checked_at": log.checked_at,
                "alert_status": _alert_status(
                    db,
                    {
                        "API": float(log.response_time_ms),
                        "Error": float(log.error_count),
                    },
                ),
            }
            for log in logs
        ]
        if not data:
            data.append(
                {
                    "service_name": "Marketplace API",
                    "health_status": HealthStatus.Healthy.value,
                    "response_time_ms": 0,
                    "error_count": 0,
                    "checked_at": None,
                    "alert_status": "Not Triggered",
                }
            )
        return {"success": True, "message": "API health fetched successfully", "data": data}
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def get_queue_health_service(db: Session):
    try:
        log = SystemHealthLog(
            service_name="Background Jobs",
            service_type=ServiceType.Queue,
            health_status=HealthStatus.Healthy,
            response_time_ms=0,
            error_count=0,
        )
        if db is not None:
            log = create_health_log(db, log)
        return {
            "success": True,
            "message": "Queue health fetched successfully",
            "data": _log_to_dict(log, "Not Triggered"),
        }
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def get_error_summary_service(db: Session):
    try:
        rows = summarize_errors(db) if db is not None else []
        data = []
        total_errors = 0
        for row in rows:
            error_count = int(row.error_count)
            total_errors += error_count
            data.append(
                {
                    "service_name": row.service_name,
                    "severity_level": row.severity_level.value,
                    "error_count": error_count,
                    "latest_error": get_latest_error_message(db, row.service_name, row.severity_level),
                }
            )
        return {
            "success": True,
            "message": "Error summary fetched successfully",
            "total_errors": total_errors,
            "data": data,
        }
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def create_alert_config_service(db: Session, payload):
    try:
        config = AlertConfiguration(
            alert_name=payload.alert_name,
            metric_type=payload.metric_type.value,
            threshold_value=Decimal(str(payload.threshold_value)),
            notification_type=payload.notification_type.value,
            is_active=True,
        )
        config = create_alert_config(db, config)
        return {
            "success": True,
            "message": "Alert configuration saved successfully",
            "data": {
                "alert_id": str(config.id),
                "alert_name": config.alert_name,
                "metric_type": config.metric_type.value,
                "threshold_value": float(config.threshold_value),
                "notification_type": config.notification_type.value,
                "is_active": config.is_active,
                "alert_status": "Active",
                "created_at": config.created_at,
                "updated_at": config.updated_at,
            },
        }
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def get_metrics_service(db: Session):
    try:
        logs = list_latest_health_logs(db, limit=50) if db is not None else []
        error_count = count_recent_errors(db) if db is not None else 0
        if not logs:
            disk_usage = _disk_usage_percent()
            return {
                "success": True,
                "message": "Metrics fetched successfully",
                "data": {
                    "health_status": _system_status(disk_usage=disk_usage, error_count=error_count).value,
                    "response_time_ms": 0,
                    "error_count": error_count,
                    "average_response_time_ms": 0,
                    "cpu_usage": 0,
                    "memory_usage": 0,
                    "disk_usage": float(disk_usage),
                    "alert_status": "Not Triggered",
                },
            }

        latest = logs[0]
        average_response = sum(log.response_time_ms for log in logs) / len(logs)
        latest_cpu = float(latest.cpu_usage) if latest.cpu_usage is not None else None
        latest_memory = float(latest.memory_usage) if latest.memory_usage is not None else None
        latest_disk = float(latest.disk_usage) if latest.disk_usage is not None else None
        alert_status = _alert_status(
            db,
            {
                "CPU": latest_cpu or 0,
                "Memory": latest_memory or 0,
                "API": float(latest.response_time_ms),
                "Error": float(error_count),
            },
        )
        return {
            "success": True,
            "message": "Metrics fetched successfully",
            "data": {
                "health_status": latest.health_status.value,
                "response_time_ms": latest.response_time_ms,
                "error_count": error_count,
                "average_response_time_ms": round(average_response, 2),
                "cpu_usage": latest_cpu,
                "memory_usage": latest_memory,
                "disk_usage": latest_disk,
                "alert_status": alert_status,
            },
        }
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))
