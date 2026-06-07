from datetime import datetime
from pydantic import BaseModel
from uuid import UUID
from typing import Optional


class AuditExportResponse(BaseModel):
    audit_log_id: UUID
    action_type: str
    module_name: str
    action_status: str
    created_at: datetime

class AuditExportListResponse(BaseModel):
    total_records: int
    records: list[AuditExportResponse]

class AuditLogResponse(BaseModel):
    id: UUID
    user_id: UUID
    user_role: str
    action_type: str
    module_name: str
    entity_type: str
    entity_id: UUID
    old_value: dict | None
    new_value: dict | None
    ip_address: str | None
    device_info: str | None
    action_status: str
    created_at: datetime

    class Config:
        from_attributes = True

class AuditLogListResponse(BaseModel):
    total_records: int
    records: list[AuditLogResponse]

class AuthenticationLogResponse(BaseModel):
    id: UUID
    user_id: UUID
    login_type: str
    ip_address: str | None
    device_info: str | None
    login_status: str
    created_at: datetime

    class Config:
        from_attributes = True

class UserAuthenticationLogsResponse(BaseModel):
    total_records: int
    records: list[AuthenticationLogResponse]

class AuditLogResponse(BaseModel):
    id: UUID
    user_id: UUID
    user_role: str
    action_type: str
    module_name: str
    entity_type: str
    entity_id: UUID
    old_value: dict | None
    new_value: dict | None
    ip_address: str | None
    device_info: str | None
    action_status: str
    created_at: datetime

    class Config:
        from_attributes = True

class EntityAuditLogListResponse(BaseModel):
    total_records: int
    records: list[AuditLogResponse]

class AuditExportRequest(BaseModel):
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    module_name: Optional[str] = None
    user_id: Optional[UUID] = None