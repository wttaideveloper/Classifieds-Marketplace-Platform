from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, List
from uuid import UUID
from datetime import datetime, date
from enum import Enum
from decimal import Decimal
from enum import Enum as PyEnum

class BookingStatus(str, PyEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# REGISTER
class CustomerRegister(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    mobile_number: str
    password: str
    confirm_password: str
    accept_terms: bool
    accept_privacy_policy: bool

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
    reset_token: str
    new_password: str
    confirm_password: str

#  CHANGE PASSWORD
class ChangePassword(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v, values):

        if (
            "new_password" in values.data
            and v != values.data["new_password"]
        ):
            raise ValueError(
                "Passwords do not match"
            )

        return v

# PROFILE
class CustomerProfileResponse(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    email: str
    mobile_number: str

    class Config:
        from_attributes = True

class CustomerProfileUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    mobile_number: Optional[str] = None
    profile_image: Optional[str] = None
    addresses: Optional[str] = None

# ADDRESS
class AddressBase(BaseModel):
    address_line_1: str
    address_line_2: Optional[str]
    city: str
    state: str
    zip_code: str
    country: str
    is_default: bool = False

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
    listing_type: Optional[str] = None
    city: Optional[str] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    page: int = 1
    limit: int = 10
    sort_by: Optional[str] = "latest"

class GetListingDetailsSchema(BaseModel):

    listing_id: UUID

class SearchListingsResponseData(BaseModel):

    id: UUID

    business_id: UUID

    listing_type: str

    title: str

    description: Optional[str]

    category_id: Optional[UUID]

    subcategory_id: Optional[UUID]

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