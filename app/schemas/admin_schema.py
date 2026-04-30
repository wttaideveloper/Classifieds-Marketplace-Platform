# app/schemas/admin_schema.py
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional, List
from datetime import datetime

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

