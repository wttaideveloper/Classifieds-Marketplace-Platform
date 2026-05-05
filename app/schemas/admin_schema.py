# app/schemas/admin_schema.py
from pydantic import BaseModel, EmailStr, field_validator, Field
from typing import Optional, List
from datetime import datetime
from uuid import UUID

class AdminLogin(BaseModel):
    email: EmailStr
    password: str


class ForgotPassword(BaseModel):
    email: EmailStr
    role: str


class ResetPassword(BaseModel):
    resetToken: str
    newPassword: str
    confirmPassword: str


class ChangePassword(BaseModel):
    currentPassword: str
    newPassword: str
    confirmPassword: str

class AdminProfileUpdate(BaseModel):
    name: str
    email: EmailStr

class UserListItem(BaseModel):
    id: str
    name: Optional[str]
    email: str
    role: str
    status: str
    createdAt: datetime


class PaginatedUsers(BaseModel):
    total: int
    page: int
    limit: int
    data: List[UserListItem]

class UserDetailsResponse(BaseModel):
    id: str
    name: Optional[str]
    email: str
    role: str
    status: str
    createdAt: datetime

class UpdateUserStatusSchema(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, value):
        allowed = ["active", "inactive", "suspended", "pending"]

        if value.lower() not in allowed:
            raise ValueError(
                "Status must be active, inactive, suspended or pending"
            )

        return value.lower()
    
class MerchantListItem(BaseModel):
    id: str
    name: Optional[str]
    email: str
    status: str
    createdAt: datetime


class MerchantListResponse(BaseModel):
    total: int
    page: int
    limit: int
    data: List[MerchantListItem]

class MerchantDetailsResponse(BaseModel):
    id: str
    name: Optional[str]
    email: str
    mobileNumber: Optional[str]
    status: str
    createdAt: datetime

class BusinessResponse(BaseModel):
    id: UUID
    name: str
    category: Optional[str]
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class BusinessListResponse(BaseModel):
    total: int
    page: int
    limit: int
    data: List[BusinessResponse]

class BusinessDetailResponse(BaseModel):
    id: UUID
    name: str
    category: Optional[str]
    status: str
    created_at: datetime

    class Config:
        from_attributes = True

class BusinessApproveResponse(BaseModel):
    id: UUID
    status: str
    approved_at: Optional[datetime]

    class Config:
        from_attributes = True

class BusinessRejectRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=500)


class BusinessRejectResponse(BaseModel):
    id: UUID
    status: str
    rejection_reason: Optional[str]
    rejected_at: Optional[datetime]

    class Config:
        from_attributes = True

class BusinessSuspendRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=500)


class BusinessSuspendResponse(BaseModel):
    id: UUID
    status: str
    suspension_reason: Optional[str]
    suspended_at: Optional[datetime]

    class Config:
        from_attributes = True

class BusinessReactivateResponse(BaseModel):
    id: UUID
    status: str
    suspended_at: Optional[datetime]

    class Config:
        from_attributes = True

class MerchantResponse(BaseModel):
    id: UUID
    name: str
    email: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True