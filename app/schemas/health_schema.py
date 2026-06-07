from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ServiceType(str, Enum):
    API = "API"
    Database = "Database"
    Queue = "Queue"
    Server = "Server"


class HealthStatus(str, Enum):
    Healthy = "Healthy"
    Warning = "Warning"
    Critical = "Critical"


class SeverityLevel(str, Enum):
    Low = "Low"
    Medium = "Medium"
    High = "High"
    Critical = "Critical"


class MetricType(str, Enum):
    CPU = "CPU"
    Memory = "Memory"
    API = "API"
    Error = "Error"


class NotificationType(str, Enum):
    Email = "Email"
    SMS = "SMS"
    Push = "Push"


class AlertConfigCreate(BaseModel):
    alert_name: str = Field(..., min_length=1, max_length=255)
    metric_type: MetricType
    threshold_value: float = Field(..., gt=0)
    notification_type: NotificationType


class HealthCheckData(BaseModel):
    service_name: str
    service_type: ServiceType
    health_status: HealthStatus
    response_time_ms: int = Field(..., ge=0)
    cpu_usage: Optional[float] = None
    memory_usage: Optional[float] = None
    disk_usage: Optional[float] = None
    error_count: int = Field(..., ge=0)
    checked_at: Optional[datetime] = None
    alert_status: str


class HealthCheckResponse(BaseModel):
    success: bool
    message: str
    data: HealthCheckData


class ApiHealthItem(BaseModel):
    service_name: str
    health_status: HealthStatus
    response_time_ms: int = Field(..., ge=0)
    error_count: int = Field(..., ge=0)
    checked_at: Optional[datetime] = None
    alert_status: str


class ApiHealthResponse(BaseModel):
    success: bool
    message: str
    data: List[ApiHealthItem]


class ErrorSummaryItem(BaseModel):
    service_name: str
    severity_level: SeverityLevel
    error_count: int = Field(..., ge=0)
    latest_error: Optional[str] = None


class ErrorSummaryResponse(BaseModel):
    success: bool
    message: str
    total_errors: int = Field(..., ge=0)
    data: List[ErrorSummaryItem]


class AlertConfigData(BaseModel):
    alert_id: str
    alert_name: str
    metric_type: MetricType
    threshold_value: float
    notification_type: NotificationType
    is_active: bool
    alert_status: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AlertConfigResponse(BaseModel):
    success: bool
    message: str
    data: AlertConfigData


class MetricsData(BaseModel):
    health_status: HealthStatus
    response_time_ms: int = Field(..., ge=0)
    error_count: int = Field(..., ge=0)
    average_response_time_ms: float = Field(..., ge=0)
    cpu_usage: Optional[float] = None
    memory_usage: Optional[float] = None
    disk_usage: Optional[float] = None
    alert_status: str


class MetricsResponse(BaseModel):
    success: bool
    message: str
    data: MetricsData
