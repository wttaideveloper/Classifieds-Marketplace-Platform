from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from uuid import UUID
from datetime import datetime, date
from enum import Enum
from decimal import Decimal
from enum import Enum as PyEnum

class BookingStatus(str, PyEnum):
    Pending = "Pending"
    Approved = "Approved"
    Rejected = "Rejected"
    Completed = "Completed"
    Cancelled = "Cancelled"


# REGISTER
class CustomerRegister(BaseModel):
    firstName: str = Field(..., examples=["John"])
    lastName: str = Field(..., examples=["Doe"])
    email: EmailStr = Field(..., examples=["john@example.com"])
    mobileNumber: str = Field(..., examples=["+1234567890"])
    password: str = Field(..., examples=["Password@123"])
    confirmPassword: str = Field(..., examples=["Password@123"])
    acceptTerms: bool = Field(..., examples=[True])
    acceptPrivacyPolicy: bool = Field(..., examples=[True])

# LOGIN
class CustomerLogin(BaseModel):
    email: EmailStr = Field(..., examples=["john@example.com"])
    password: str = Field(..., examples=["Password@123"])

# FORGOT PASSWORD
class ForgotPassword(BaseModel):
    email: EmailStr = Field(..., examples=["john@example.com"])
    role: str = Field(..., examples=["customer"])


#  RESET PASSWORD
class ResetPassword(BaseModel):
    resetToken: str = Field(..., examples=["your-reset-token"])
    newPassword: str = Field(..., examples=["NewPassword@123"])
    confirmPassword: str = Field(..., examples=["NewPassword@123"])


#  CHANGE PASSWORD
class ChangePassword(BaseModel):
    currentPassword: str = Field(..., examples=["OldPassword@123"])
    newPassword: str = Field(..., examples=["NewPassword@123"])
    confirmPassword: str = Field(..., examples=["NewPassword@123"])

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

class CustomerBookingResponse(BaseModel):
    booking_id: UUID
    listing_name: str
    booking_date: date
    booking_status: BookingStatus
    total_amount: Decimal

    class Config:
        from_attributes = True

class CustomerBookingList(BaseModel):
    total_records: int
    page: int
    size: int
    bookings: List[CustomerBookingResponse]