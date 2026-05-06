# app/schemas/admin_schema.py
from pydantic import BaseModel, EmailStr, field_validator, Field
from typing import Optional, List
from datetime import datetime
from uuid import UUID

#  AUTH 

class AdminLogin(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)


class ForgotPassword(BaseModel):
    email: EmailStr
    role: str

    @field_validator("role")
    @classmethod
    def validate_role(cls, value):
        allowed = ["admin", "user", "merchant"]
        if value.lower() not in allowed:
            raise ValueError("Invalid role")
        return value.lower()


class ResetPassword(BaseModel):
    reset_token: str
    new_password: str = Field(..., min_length=6)
    confirm_password: str

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v, values):
        if "new_password" in values and v != values["new_password"]:
            raise ValueError("Passwords do not match")
        return v


class ChangePassword(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=6)
    confirm_password: str

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v, values):
        if "new_password" in values and v != values["new_password"]:
            raise ValueError("Passwords do not match")
        return v


# ADMIN 

class AdminProfileUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None


#  USERS 

class UserListItem(BaseModel):
    id: UUID
    name: Optional[str] = None
    email: str
    role: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class PaginatedUsers(BaseModel):
    total: int
    page: int
    limit: int
    data: List[UserListItem]


class UserDetailsResponse(BaseModel):
    id: UUID
    name: Optional[str] = None
    email: str
    role: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class UpdateUserStatusSchema(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, value):
        allowed = ["active", "inactive", "suspended", "pending"]
        if value.lower() not in allowed:
            raise ValueError("Invalid status")
        return value.lower()


#  MERCHANT 

class MerchantListItem(BaseModel):
    id: UUID
    full_name: Optional[str] = None
    business_email: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class MerchantListResponse(BaseModel):
    total: int
    page: int
    limit: int
    data: List[MerchantListItem]


class MerchantDetailsResponse(BaseModel):
    id: UUID
    full_name: Optional[str] = None
    business_email: str
    mobile_number: Optional[str] = None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


#  BUSINESS 

class BusinessResponse(BaseModel):
    id: UUID
    name: str
    category: Optional[str] = None
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
    category: Optional[str] = None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class BusinessApproveResponse(BaseModel):
    id: UUID
    status: str
    approved_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class BusinessRejectRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=500)


class BusinessRejectResponse(BaseModel):
    id: UUID
    status: str
    rejection_reason: Optional[str] = None
    rejected_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class BusinessSuspendRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=500)


class BusinessSuspendResponse(BaseModel):
    id: UUID
    status: str
    suspension_reason: Optional[str] = None
    suspended_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class BusinessReactivateResponse(BaseModel):
    id: UUID
    status: str
    suspended_at: Optional[datetime] = None

    class Config:
        from_attributes = True


#  RELATION 

class MerchantResponse(BaseModel):
    id: UUID
    full_name: str
    business_email: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True