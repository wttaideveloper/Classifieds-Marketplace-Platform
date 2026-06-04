import enum
import uuid

from sqlalchemy import Boolean, Column, DateTime, Enum, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.database import Base


class ServiceType(str, enum.Enum):
    API = "API"
    Database = "Database"
    Queue = "Queue"
    Server = "Server"


class HealthStatus(str, enum.Enum):
    Healthy = "Healthy"
    Warning = "Warning"
    Critical = "Critical"


class SeverityLevel(str, enum.Enum):
    Low = "Low"
    Medium = "Medium"
    High = "High"
    Critical = "Critical"


class MetricType(str, enum.Enum):
    CPU = "CPU"
    Memory = "Memory"
    API = "API"
    Error = "Error"


class NotificationType(str, enum.Enum):
    Email = "Email"
    SMS = "SMS"
    Push = "Push"


class SystemHealthLog(Base):
    __tablename__ = "system_health_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_name = Column(String(255), nullable=False, index=True)
    service_type = Column(Enum(ServiceType), nullable=False, index=True)
    health_status = Column(Enum(HealthStatus), nullable=False, index=True)
    response_time_ms = Column(Integer, nullable=False, default=0)
    cpu_usage = Column(Numeric(5, 2), nullable=True)
    memory_usage = Column(Numeric(5, 2), nullable=True)
    disk_usage = Column(Numeric(5, 2), nullable=True)
    error_count = Column(Integer, nullable=False, default=0)
    checked_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ErrorLog(Base):
    __tablename__ = "error_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_name = Column(String(255), nullable=False, index=True)
    error_message = Column(Text, nullable=False)
    stack_trace = Column(Text, nullable=True)
    severity_level = Column(Enum(SeverityLevel), nullable=False, index=True)
    request_path = Column(String(500), nullable=True)
    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    ip_address = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class AlertConfiguration(Base):
    __tablename__ = "alert_configurations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_name = Column(String(255), nullable=False)
    metric_type = Column(Enum(MetricType), nullable=False, index=True)
    threshold_value = Column(Numeric(10, 2), nullable=False)
    notification_type = Column(Enum(NotificationType), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
