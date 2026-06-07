from pydantic import BaseModel, EmailStr
from uuid import UUID
from datetime import datetime
from typing import Optional
from enum import Enum


class StaffStatusEnum(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    PENDING = "pending"

class StaffCreate(BaseModel):
    merchant_id: UUID
    first_name: str
    last_name: str
    email: EmailStr
    phone_number: str
    role_id: UUID
    invited_by: UUID

class StaffResponse(BaseModel):
    id: UUID
    merchant_id: UUID
    first_name: str
    last_name: str
    email: str
    phone_number: str
    role_id: UUID
    staff_status: str
    invited_by: UUID
    joined_at: datetime | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class StaffCreateResponse(BaseModel):
    success: bool
    message: str
    data: StaffResponse

class StaffListData(BaseModel):
    id: UUID
    merchant_id: UUID
    first_name: str
    last_name: str
    email: str
    phone_number: str
    role_id: UUID
    staff_status: str
    invited_by: UUID
    joined_at: datetime | None
    invitation_status: str | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime

class StaffListResponse(BaseModel):
    success: bool
    message: str
    data: list[StaffListData]

class StaffUpdateRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = None
    role_id: Optional[UUID] = None
    staff_status: Optional[str] = None

class StaffUpdateResponse(BaseModel):
    id: UUID
    merchant_id: UUID
    first_name: str
    last_name: str
    email: str
    phone_number: str
    role_id: UUID
    staff_status: str
    invited_by: UUID
    joined_at: datetime | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class StaffDetailResponse(BaseModel):
    success: bool
    message: str
    data: StaffResponse

class StaffDeleteResponse(BaseModel):
    success: bool
    message: str

class UpdateStaffStatusRequest(BaseModel):
    staff_status: StaffStatusEnum


class StaffStatusData(BaseModel):
    staff_id: UUID
    staff_status: StaffStatusEnum


class StaffStatusResponse(BaseModel):
    success: bool
    message: str
    data: StaffStatusData

class AssignRoleRequest(BaseModel):
    role_id: UUID


class AssignRoleData(BaseModel):
    staff_id: UUID
    role_id: UUID
    staff_status: str
    joined_at: datetime | None


class AssignRoleResponse(BaseModel):
    success: bool
    message: str
    data: AssignRoleData