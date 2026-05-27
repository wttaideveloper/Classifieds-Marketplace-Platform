from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from decimal import Decimal
from uuid import UUID
from datetime import datetime, date, time
from enum import Enum as PyEnum

class ListingType(str, PyEnum):
    PRODUCT = "product"
    SERVICE = "service"
    EVENT = "event"
    TRAINING = "training"
    PROGRAM = "program"

class BookingStatus(str, PyEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class PaymentStatus(str, PyEnum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"
    
class RefreshTokenSchema(BaseModel):
    refresh_token: str

class VerifyEmailSchema(BaseModel):
    verification_token: str

class ResendVerificationSchema(BaseModel):
    email: EmailStr

class PublicListingResponse(BaseModel):

    id: UUID
    business_Id: UUID
    listing_type: str
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
    business_id: UUID
    listing_type: str
    title: str
    description: Optional[str]
    category_id: Optional[UUID]
    subcategory_id: Optional[UUID]
    price: Optional[float]
    currency: Optional[str]
    images: List[str]
    status: str
    tags: List[str]
    duration: Optional[str]
    service_mode: Optional[str]
    availability: Optional[str]
    schedule: Optional[str]
    start_date: Optional[datetime]
    end_date: Optional[datetime]
    capacity: Optional[int]
    location: Optional[str]
    isOnline: Optional[bool]
    registration_deadline: Optional[datetime]

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
    is_active: bool
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
    parent_category_id: Optional[UUID]

    class Config:
        from_attributes = True

class SubCategoryListResponse(BaseModel):

    success: bool
    message: str
    total: int
    data: List[SubcategoryResponse]

class UploadedListingImage(BaseModel):
    file_name: str
    file_path: str

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