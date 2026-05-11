from pydantic import BaseModel, EmailStr
from typing import Optional, List
from uuid import UUID
from datetime import datetime

# REGISTER
class CustomerRegister(BaseModel):
    firstName: str
    lastName: str
    email: EmailStr
    mobileNumber: str
    password: str
    confirmPassword: str
    acceptTerms: bool
    acceptPrivacyPolicy: bool

# LOGIN
class CustomerLogin(BaseModel):
    email: EmailStr
    password: str

# FORGOT PASSWORD
class ForgotPassword(BaseModel):
    email: EmailStr
    role: str


#  RESET PASSWORD
class ResetPassword(BaseModel):
    resetToken: str
    newPassword: str
    confirmPassword: str


#  CHANGE PASSWORD
class ChangePassword(BaseModel):
    currentPassword: str
    newPassword: str
    confirmPassword: str

# PROFILE
class CustomerProfileUpdate(BaseModel):
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    mobileNumber: Optional[str] = None
    profileImage: Optional[str] = None
    addresses: Optional[str] = None

# ADDRESS
class AddressBase(BaseModel):
    addressLine1: str
    addressLine2: Optional[str]
    city: str
    state: str
    zipCode: str
    country: str
    isDefault: bool = False

class PublicListingQuerySchema(BaseModel):

    search: Optional[str] = None
    category: Optional[str] = None
    listingType: Optional[str] = None
    city: Optional[str] = None
    priceMin: Optional[float] = None
    priceMax: Optional[float] = None
    page: int = 1
    limit: int = 10
    sortBy: Optional[str] = "latest"

class GetListingDetailsSchema(BaseModel):

    listingId: UUID

class SearchListingsResponseData(BaseModel):

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

    tags: List[str]

    location: Optional[str]

    created_at: Optional[datetime]

    class Config:
        from_attributes = True


class SearchListingsResponse(BaseModel):

    success: bool

    message: str

    total: int

    data: List[SearchListingsResponseData]