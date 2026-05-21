from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from decimal import Decimal
from uuid import UUID
from datetime import datetime, date, time
from enum import Enum
from enum import Enum as PyEnum

class ListingType(str, PyEnum):
    product = "product"
    service = "service"
    event = "event"
    training = "training"
    program = "program"

class BookingStatus(str, PyEnum):
    Pending = "Pending"
    Approved = "Approved"
    Rejected = "Rejected"
    Completed = "Completed"
    Cancelled = "Cancelled"

class PaymentStatus(str, PyEnum):
    Pending = "Pending"
    Paid = "Paid"
    Failed = "Failed"
    Refunded = "Refunded"
    
class RefreshTokenSchema(BaseModel):
    refreshToken: str

class VerifyEmailSchema(BaseModel):
    verificationToken: str

class ResendVerificationSchema(BaseModel):
    email: EmailStr

class PublicListingResponse(BaseModel):

    id: UUID
    businessId: UUID
    listingType: str
    title: str
    description: Optional[str]
    category: Optional[str]
    city: Optional[str]
    price: Optional[float]
    currency: Optional[str]
    status: str

    class Config:
        from_attributes = True

class PublicListingPaginationResponse(BaseModel):

    success: bool
    message: str
    total: int
    page: int
    limit: int
    data: List[PublicListingResponse]

class PublicListingDetailsResponse(BaseModel):

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
    duration: Optional[str]
    serviceMode: Optional[str]
    availability: Optional[str]
    schedule: Optional[str]
    startDate: Optional[datetime]
    endDate: Optional[datetime]
    capacity: Optional[int]
    location: Optional[str]
    isOnline: Optional[bool]
    registrationDeadline: Optional[datetime]

    class Config:
        from_attributes = True


class PublicListingDetailsMainResponse(BaseModel):

    success: bool
    message: str
    data: PublicListingDetailsResponse

class PaginationSchema(BaseModel):

    page: Optional[int] 
    limit: Optional[int] 

class CategoryData(BaseModel):

    id: UUID
    name: str
    description: Optional[str]
    icon: Optional[str]
    isActive: bool
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class CategoryListResponse(BaseModel):

    success: bool
    message: str
    total: int
    data: List[CategoryData]

class CategoryResponse(BaseModel):

    id: UUID
    name: str
    description: Optional[str]
    icon: Optional[str]

    class Config:
        from_attributes = True


class SubcategoryResponse(BaseModel):

    id: UUID
    name: str
    description: Optional[str]
    icon: Optional[str]
    parentCategoryId: Optional[UUID]

    class Config:
        from_attributes = True

class SubCategoryListResponse(BaseModel):

    success: bool
    message: str
    total: int
    data: List[SubcategoryResponse]

class UploadedListingImage(BaseModel):
    fileName: str
    filePath: str

class UploadListingImagesResponse(BaseModel):
    success: bool
    message: str
    data: List[UploadedListingImage]

class CreateBooking(BaseModel):

    customer_id: Optional[UUID] = None
    merchant_id: Optional[UUID] = None
    business_id: Optional[UUID] = None
    listing_id: UUID
    booking_date: date
    booking_time: time
    quantity: int
    notes: Optional[str] = None

class CreateBookingResponse(BaseModel):

    booking_id: UUID
    booking_number: str
    booking_status: BookingStatus
    total_amount: Decimal