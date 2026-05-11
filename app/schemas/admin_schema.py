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

# admin_schema.py

from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID
from datetime import datetime


class AdminListingData(BaseModel):

    id: UUID

    businessId: UUID

    listingType: str

    title: str

    description: Optional[str]

    categoryId: Optional[UUID]

    subcategoryId: Optional[UUID]

    price: Optional[float]

    currency: Optional[str]

    images: List[str]

    status: str

    tags: List[str]

    location: Optional[str]

    created_at: Optional[datetime]

    class Config:
        from_attributes = True


class AdminGetAllListingsResponse(BaseModel):

    success: bool
    message: str
    total: int
    data: List[AdminListingData]

class ApproveListingData(BaseModel):

    id: UUID
    businessId: UUID
    listingType: str
    title: str
    status: str
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True

class ApproveListingResponse(BaseModel):

    success: bool
    message: str
    data: ApproveListingData

class RejectListingRequest(BaseModel):

    reason: str

class RejectListingData(BaseModel):

    id: UUID

    businessId: UUID

    listingType: str

    title: str

    status: str

    rejectionReason: Optional[str]

    updated_at: Optional[datetime]

    class Config:
        from_attributes = True

class RejectListingResponse(BaseModel):

    success: bool
    message: str
    data: RejectListingData

class SuspendListingRequest(BaseModel):

    reason: Optional[str] = None

class SuspendListingData(BaseModel):

    id: UUID
    status: str
    suspendedAt: Optional[datetime]
    suspensionReason: Optional[str]

    class Config:
        from_attributes = True

class SuspendListingResponse(BaseModel):

    success: bool
    message: str
    data: SuspendListingData

class ReactivateListingData(BaseModel):

    id: UUID
    status: str
    suspendedAt: Optional[datetime]
    suspensionReason: Optional[str]

    class Config:
        from_attributes = True

class ReactivateListingResponse(BaseModel):

    success: bool
    message: str
    data: ReactivateListingData

# CREATE CATEGORY
class CreateCategorySchema(BaseModel):

    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    isActive: bool = True


# CATEGORY RESPONSE
class CategoryResponse(BaseModel):

    id: UUID
    name: str
    description: Optional[str]
    icon: Optional[str]
    isActive: bool
    createdAt: datetime

    class Config:
        from_attributes = True


# MAIN RESPONSE
class CreateCategoryResponse(BaseModel):

    success: bool
    message: str
    data: CategoryResponse