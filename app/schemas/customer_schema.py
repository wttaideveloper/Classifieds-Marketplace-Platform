from pydantic import BaseModel, EmailStr, Field, field_validator
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
    @field_validator("confirmPassword")
    @classmethod
    def passwords_match(cls, v, values):
        if "password" in values.data and v != values.data["password"]:
            raise ValueError("Passwords do not match")
        if "newPassword" in values.data and v != values.data["newPassword"]:
            raise ValueError("Passwords do not match")
        return v

# PROFILE
class CustomerProfileResponse(BaseModel):
    id: UUID
    firstName: str
    lastName: str
    email: str
    mobileNumber: str

    class Config:
        from_attributes = True

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

# CREATE ADDRESS REQUEST
class AddressCreate(AddressBase):
    pass

# ADDRESS RESPONSE
class AddressResponse(AddressBase):
    id: UUID
    customer_id: UUID

    class Config:
        from_attributes = True

# UPDATE ADDRESS REQUEST
class AddressUpdate(AddressBase):
    pass

# GET ALL ADDRESSES RESPONSE
class AddressListResponse(BaseModel):
    success: bool
    message: str
    data: list[AddressResponse]

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