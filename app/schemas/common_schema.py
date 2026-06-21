from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Dict, Any, Union
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


class EnterpriseBasePayload(BaseModel):
    business_legal_name: Optional[str] = None
    business_short_name: Optional[str] = None
    business_description: Optional[str] = None
    business_email: Optional[EmailStr] = None
    business_phone: Optional[str] = None
    registered_address: Optional[str] = None
    business_address: Optional[str] = None
    communication_address: Optional[str] = None
    logo_url: Optional[str] = None
    business_images: Optional[Union[str, List[str]]] = None
    registration_number: Optional[str] = None
    business_category: Optional[str] = None
    website_url: Optional[str] = None
    year_founded: Optional[Union[int, str]] = None
    primary_contact_name: Optional[str] = None
    primary_contact_title: Optional[str] = None
    secondary_email: Optional[EmailStr] = None
    secondary_phone: Optional[str] = None
    suite_unit: Optional[str] = None
    brand_color: Optional[str] = None
    tagline: Optional[str] = None


class EnterpriseCreatePayload(EnterpriseBasePayload):
    business_legal_name: str
    business_short_name: str
    business_description: str
    business_email: EmailStr
    business_phone: str
    registered_address: str
    logo_url: Optional[str] = None


class EnterpriseUpdatePayload(EnterpriseBasePayload):
    pass


class EnterpriseListItem(BaseModel):
    id: UUID
    name: str
    businessLegalName: Optional[str] = None
    businessShortName: Optional[str] = None
    businessDescription: Optional[str] = None
    businessEmail: Optional[str] = None
    businessPhone: Optional[str] = None
    registeredAddress: Optional[str] = None
    businessAddress: Optional[str] = None
    communicationAddress: Optional[str] = None
    logoUrl: Optional[str] = None
    businessImages: List[str] = Field(default_factory=list)
    registrationNumber: Optional[str] = None
    businessCategory: Optional[str] = None
    websiteUrl: Optional[str] = None
    yearFounded: Optional[Union[int, str]] = None
    primaryContactName: Optional[str] = None
    primaryContactTitle: Optional[str] = None
    secondaryEmail: Optional[str] = None
    secondaryPhone: Optional[str] = None
    suiteUnit: Optional[str] = None
    brandColor: Optional[str] = None
    tagline: Optional[str] = None
    category: Optional[str] = None
    status: str
    membersCount: int
    revenue: float
    joinedDate: Optional[datetime] = None


class EnterpriseListResponse(BaseModel):
    success: bool
    message: str
    total: int
    page: int
    limit: int
    data: List[EnterpriseListItem]


class EnterpriseDetails(EnterpriseListItem):
    rating: Optional[float] = None


class EnterpriseDetailsResponse(BaseModel):
    success: bool
    message: str
    data: EnterpriseDetails


class ProductBasePayload(BaseModel):
    enterprise_id: Optional[UUID] = None
    product_name: Optional[str] = None
    product_description: Optional[str] = None
    product_category: Optional[str] = None
    product_price: Optional[Union[float, str]] = None
    product_images: Optional[Union[str, List[str]]] = None
    product_status: Optional[bool] = None
    sku: Optional[str] = None
    barcode_upc: Optional[str] = None
    weight: Optional[Union[float, str]] = None
    dimensions: Optional[str] = None
    sale_price: Optional[Union[float, str]] = None
    cost_price: Optional[Union[float, str]] = None
    tax_class: Optional[str] = None
    currency: Optional[str] = None
    stock_quantity: Optional[Union[int, str]] = None
    low_stock_alert_threshold: Optional[Union[int, str]] = None
    stock_management: Optional[bool] = None
    publish_status: Optional[str] = None


class ProductCreatePayload(ProductBasePayload):
    enterprise_id: UUID
    product_name: str
    product_description: str
    product_category: str
    product_price: Union[float, str]
    product_status: bool


class ProductUpdatePayload(ProductBasePayload):
    pass


class ProductListItem(BaseModel):
    id: UUID
    businessId: UUID
    listingType: str
    title: str
    description: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    images: List[str] = Field(default_factory=list)
    status: str
    rating: Optional[float] = None
    productCategory: Optional[str] = None
    sku: Optional[str] = None
    barcodeUpc: Optional[str] = None
    weight: Optional[Union[float, str]] = None
    dimensions: Optional[str] = None
    salePrice: Optional[Union[float, str]] = None
    costPrice: Optional[Union[float, str]] = None
    taxClass: Optional[str] = None
    stockQuantity: Optional[int] = None
    lowStockAlertThreshold: Optional[Union[int, str]] = None
    stockManagement: Optional[bool] = None
    publishStatus: Optional[str] = None


class ProductListResponse(BaseModel):
    success: bool
    message: str
    total: int
    page: int
    limit: int
    data: List[ProductListItem]


class ProductDetails(ProductListItem):
    enterpriseName: Optional[str] = None
    length: Optional[float] = None
    width: Optional[float] = None
    thick: Optional[float] = None
    stockCount: Optional[int] = None


class ProductDetailsResponse(BaseModel):
    success: bool
    message: str
    data: ProductDetails


class ServiceScheduleItem(BaseModel):
    day: str
    is_available: bool
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    slot_length: Optional[str] = None


class ServiceBasePayload(BaseModel):
    enterprise_id: Optional[UUID] = None
    service_name: Optional[str] = None
    service_description: Optional[str] = None
    service_category: Optional[str] = None
    service_price: Optional[Union[float, str]] = None
    duration: Optional[str] = None
    availability_status: Optional[bool] = None
    service_status: Optional[bool] = None
    max_participants: Optional[Union[int, str]] = None
    provider_name: Optional[str] = None
    instructor_name: Optional[str] = None
    delivery_format: Optional[str] = None
    package_price: Optional[Union[float, str]] = None
    currency: Optional[str] = None
    cancellation_policy: Optional[str] = None
    availability_schedule: Optional[List[ServiceScheduleItem]] = None


class ServiceCreatePayload(ServiceBasePayload):
    enterprise_id: UUID
    service_name: str
    service_description: str
    service_category: str
    service_price: Union[float, str]
    duration: str
    availability_status: bool
    service_status: bool


class ServiceUpdatePayload(ServiceBasePayload):
    pass


class ServiceListItem(BaseModel):
    id: UUID
    businessId: UUID
    listingType: str
    title: str
    description: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    images: List[str] = Field(default_factory=list)
    status: str
    trainerName: Optional[str] = None
    serviceCategory: Optional[str] = None
    maxParticipants: Optional[int] = None
    providerName: Optional[str] = None
    instructorName: Optional[str] = None
    deliveryFormat: Optional[str] = None
    packagePrice: Optional[Union[float, str]] = None
    currency: Optional[str] = None
    cancellationPolicy: Optional[str] = None
    availabilitySchedule: List[ServiceScheduleItem] = Field(default_factory=list)


class ServiceListResponse(BaseModel):
    success: bool
    message: str
    total: int
    page: int
    limit: int
    data: List[ServiceListItem]


class ServiceAvailability(BaseModel):
    weekDates: List[date] = Field(default_factory=list)
    dayWiseSlotCount: Dict[str, int] = Field(default_factory=dict)
    slotTimings: List[str] = Field(default_factory=list)
    rawAvailability: Optional[Any] = None
    rawSchedule: Optional[Any] = None


class ServiceDetails(ServiceListItem):
    bannerImage: Optional[str] = None
    expertiseName: Optional[str] = None
    type: Optional[str] = None
    format: Optional[str] = None
    availability: ServiceAvailability


class ServiceDetailsResponse(BaseModel):
    success: bool
    message: str
    data: ServiceDetails