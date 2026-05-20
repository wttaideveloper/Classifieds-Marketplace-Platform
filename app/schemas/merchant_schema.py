from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Dict
from datetime import datetime, date
from uuid import UUID
from enum import Enum
from enum import Enum as PyEnum

# ENUMS
class ListingType(str, PyEnum):
    product = "product"
    service = "service"
    event = "event"
    training = "training"
    program = "program"

class ListingStatus(str, Enum):
    draft = "draft"
    published = "published"


class ServiceMode(str, Enum):
    online = "online"
    offline = "offline"
    hybrid = "hybrid"

class BookingStatus(str, PyEnum):
    Pending = "Pending"
    Approved = "Approved"
    Rejected = "Rejected"
    Completed = "Completed"
    Cancelled = "Cancelled"

# REGISTER
class MerchantRegister(BaseModel):
    fullName: str = Field(..., examples=["John Doe"])
    businessEmail: EmailStr = Field(..., examples=["john@business.com"])
    mobileNumber: str = Field(..., examples=["+1234567890"])
    password: str = Field(..., examples=["Password@123"])
    confirmPassword: str = Field(..., examples=["Password@123"])
    businessName: str = Field(..., examples=["John's Store"])
    acceptTerms: bool = Field(..., examples=[True])
    acceptPrivacyPolicy: bool = Field(..., examples=[True])

# LOGIN
class MerchantLogin(BaseModel):
    email: EmailStr = Field(..., examples=["john@business.com"])
    password: str = Field(..., examples=["Password@123"])

# FORGOT PASSWORD
class ForgotPassword(BaseModel):
    email: EmailStr = Field(..., examples=["john@business.com"])
    role: str = Field(..., examples=["merchant"])

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
class MerchantProfileUpdate(BaseModel):
    name: Optional[str] = Field(None, examples=[None])
    mobileNumber: Optional[str] = Field(None, examples=[None])
    profileImage: Optional[str] = Field(None, examples=[None])

class MerchantBusinessProfileCreate(BaseModel):    
    businessName: str    
    businessDescription: Optional[str] = None   
    primaryCategory: str    
    subcategory: Optional[str] = None    
    businessEmail: EmailStr    
    phoneNumber: str    
    fullAddress: str    
    city: str    
    state: str    
    zipCode: str    
    country: str    
    latitude: float    
    longitude: float   
    businessLogo: Optional[str] = None    
    bannerImage: Optional[str] = None    
    galleryImages: Optional[List[str]] = []    
    operatingHours: Optional[Dict] = {}    
    businessType: str   # physical | online | hybrid    
    cancellationPolicy: Optional[str] = None    
    refundPolicy: Optional[str] = None    
    merchantTermsOfService: Optional[str] = None    
    websiteUrl: Optional[str] = None    
    socialMediaLinks: Optional[Dict] = {}    
    additionalContactNumbers: Optional[List[str]] = []    
    shortTagline: Optional[str] = None

class MerchantBusinessDraft(BaseModel):
    businessName: Optional[str] = None
    businessDescription: Optional[str] = None
    primaryCategory: Optional[str] = None
    subcategory: Optional[str] = None
    businessEmail: Optional[EmailStr] = None
    phoneNumber: Optional[str] = None
    fullAddress: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zipCode: Optional[str] = None
    country: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None
    businessLogo: Optional[str] = None
    bannerImage: Optional[str] = None
    galleryImages: Optional[List[str]] = None
    operatingHours: Optional[Dict] = None
    businessType: Optional[str] = None
    cancellationPolicy: Optional[str] = None
    refundPolicy: Optional[str] = None
    merchantTermsOfService: Optional[str] = None
    websiteUrl: Optional[str] = None
    socialMediaLinks: Optional[Dict] = None
    additionalContactNumbers: Optional[List[str]] = None
    shortTagline: Optional[str] = None

class MerchantBusinessProfileResponse(BaseModel):
    id: str
    merchant_id: str
    businessName: str
    businessDescription: Optional[str]
    primaryCategory: Optional[str]
    subcategory: Optional[str]
    businessEmail: Optional[str]
    phoneNumber: Optional[str]
    fullAddress: Optional[str]
    city: Optional[str]
    state: Optional[str]
    zipCode: Optional[str]
    country: Optional[str]
    latitude: Optional[str]
    longitude: Optional[str]
    businessLogo: Optional[str]
    bannerImage: Optional[str]
    galleryImages: Optional[List[str]]
    operatingHours: Optional[Dict]
    businessType: Optional[str]
    cancellationPolicy: Optional[str]
    refundPolicy: Optional[str]
    merchantTermsOfService: Optional[str]
    websiteUrl: Optional[str]
    socialMediaLinks: Optional[Dict]
    additionalContactNumbers: Optional[List[str]]
    shortTagline: Optional[str]

    class Config:
        from_attributes = True

class BusinessStatusResponse(BaseModel):
    success: bool
    message: str
    businessStatus: str
    businessName: Optional[str] = None



class UpdateBusinessProfile(BaseModel):
    # NORMAL EDITABLE FIELDS
    # Description
    businessDescription: Optional[str] = None
    # Images
    businessLogo: Optional[str] = None
    bannerImage: Optional[str] = None
    galleryImages: Optional[List[str]] = None
    # Contact Details
    businessEmail: Optional[EmailStr] = None
    phoneNumber: Optional[str] = None
    additionalContactNumbers: Optional[List[str]] = None
    websiteUrl: Optional[str] = None
    socialMediaLinks: Optional[Dict] = None
    # Operating Hours
    operatingHours: Optional[Dict] = None
    # Policies
    cancellationPolicy: Optional[str] = None
    refundPolicy: Optional[str] = None
    merchantTermsOfService: Optional[str] = None
    # Optional branding text
    shortTagline: Optional[str] = None
    # RESTRICTED FIELDS (May Need Approval)
    # Business Name
    businessName: Optional[str] = None
    # Address / Location
    fullAddress: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zipCode: Optional[str] = None
    country: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None
    # Category
    primaryCategory: Optional[str] = None
    subcategory: Optional[str] = None

class MerchantListingBase(BaseModel):

    businessId: UUID
    listingType: ListingType
    title: str
    description: str
    categoryId: UUID
    subcategoryId: Optional[UUID] = None
    price: float
    currency: str = "INR"
    images: List[str] = Field(default_factory=list)
    status: ListingStatus = ListingStatus.draft
    tags: List[str] = Field(default_factory=list)
    # PRODUCT
    stockQuantity: Optional[int] = None
    sku: Optional[str] = None
    weight: Optional[float] = None
    # SERVICE
    duration: Optional[str] = None
    serviceMode: Optional[ServiceMode] = None
    availability: Optional[str] = None
    schedule: Optional[str] = None
    # EVENT / TRAINING / PROGRAM
    startDate: Optional[datetime] = None
    endDate: Optional[datetime] = None
    capacity: Optional[int] = None
    location: Optional[str] = None
    isOnline: bool = False
    registrationDeadline: Optional[datetime] = None

class MerchantListingCreate(MerchantListingBase):
    pass

class MerchantListingCreateResponse(MerchantListingBase):

    id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class MerchantDraftCreate(BaseModel):

    businessId: Optional[UUID] = None
    listingType: Optional[ListingType] = None
    title: Optional[str] = None
    description: Optional[str] = None
    categoryId: Optional[UUID] = None
    subcategoryId: Optional[UUID] = None
    price: Optional[float] = None
    currency: str = "INR"
    images: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    stockQuantity: Optional[int] = None
    sku: Optional[str] = None
    weight: Optional[float] = None
    duration: Optional[str] = None
    serviceMode: Optional[ServiceMode] = None
    availability: Optional[str] = None
    schedule: Optional[str] = None
    startDate: Optional[datetime] = None
    endDate: Optional[datetime] = None
    capacity: Optional[int] = None
    location: Optional[str] = None
    isOnline: bool = False
    registrationDeadline: Optional[datetime] = None

class MerchantDraftResponse(BaseModel):

    id: UUID
    businessId: Optional[UUID]
    listingType: Optional[ListingType]
    title: Optional[str]
    description: Optional[str]
    status: ListingStatus

    class Config:
        from_attributes = True

class MerchantListingListResponse(BaseModel):

    id: UUID
    businessId: UUID
    listingType: ListingType
    title: str
    description: Optional[str]
    price: Optional[float]
    currency: Optional[str]
    images: List[str]
    status: ListingStatus
    tags: List[str]
    created_at: datetime

    class Config:
        from_attributes = True

class MerchantListingPaginationResponse(BaseModel):

    success: bool
    message: str
    total: int
    page: int
    limit: int
    data: List[MerchantListingListResponse]

class MerchantListingDetailsResponse(BaseModel):

    id: UUID
    businessId: UUID
    listingType: ListingType
    title: str
    description: Optional[str]
    categoryId: Optional[UUID]
    subcategoryId: Optional[UUID]
    price: Optional[float]
    currency: Optional[str]
    images: List[str]
    status: ListingStatus
    tags: List[str]
    stockQuantity: Optional[int]
    sku: Optional[str]
    weight: Optional[float]
    duration: Optional[str]
    serviceMode: Optional[ServiceMode]
    availability: Optional[str]
    schedule: Optional[str]
    startDate: Optional[datetime]
    endDate: Optional[datetime]
    capacity: Optional[int]
    location: Optional[str]
    isOnline: bool
    registrationDeadline: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class UpdateMerchantListing(BaseModel):

    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    images: Optional[List[str]] = None
    availability: Optional[str] = None
    schedule: Optional[str] = None
    capacity: Optional[int] = None

class MerchantListingUpdateResponse(BaseModel):

    id: UUID
    title: Optional[str]
    description: Optional[str]
    price: Optional[float]
    images: Optional[List[str]]
    availability: Optional[str]
    schedule: Optional[str]
    capacity: Optional[int]
    updated_at: datetime

    class Config:
        from_attributes = True

class DeleteMerchantListingResponse(BaseModel):

    success: bool
    message: str
    deletedListingId: UUID

class PublishListingResponse(BaseModel):

    success: bool
    message: str
    data: dict

class PublishListingData(BaseModel):

    id: UUID
    status: str
    updated_at: datetime

    class Config:
        from_attributes = True

class UnpublishListingResponse(BaseModel):

    success: bool
    message: str
    data: dict

class UnpublishListingData(BaseModel):
    id: UUID
    status: str
    updated_at: datetime

    class Config:
        from_attributes = True

class UploadListingImagesResponse(BaseModel):

    success: bool
    message: str
    data: dict

class UploadListingImagesData(BaseModel):

    listingId: UUID
    images: List[str]

class DeleteListingImageResponse(BaseModel):

    success: bool
    message: str
    data: dict

class DeleteListingImageData(BaseModel):

    listingId: UUID
    deletedImage: str
    remainingImages: List[str]

# CREATE CUSTOM ATTRIBUTE
class MerchantCustomAttributeCreate(BaseModel):

    merchant_id: UUID
    attribute_id: UUID
    custom_label: Optional[str] = None
    custom_placeholder: Optional[str] = None
    is_required: Optional[bool] = False
    default_value: Optional[str] = None
    is_active: Optional[bool] = True

# RESPONSE SCHEMA
class MerchantCustomAttributeResponse(BaseModel):

    id: UUID
    merchant_id: UUID
    attribute_id: UUID
    custom_label: Optional[str]
    custom_placeholder: Optional[str]
    is_required: bool
    default_value: Optional[str]
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True

# MAP ATTRIBUTE TO BUSINESS
class BusinessAttributeMapCreate(BaseModel):

    attribute_id: UUID
    attribute_value: str

# RESPONSE
class BusinessAttributeMapResponse(BaseModel):

    id: UUID
    business_id: UUID
    attribute_id: UUID
    attribute_value: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True

# MAP ATTRIBUTE TO LISTING
class ListingAttributeMapCreate(BaseModel):

    attribute_id: UUID
    attribute_value: str

# RESPONSE
class ListingAttributeMapResponse(BaseModel):

    id: UUID
    listing_id: UUID
    attribute_id: UUID
    attribute_value: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True

class MerchantBookingResponse(BaseModel):
    booking_id: UUID
    customer_name: str
    listing_name: str
    booking_status: BookingStatus
    booking_date: date

    class Config:
        from_attributes = True


class MerchantBookingList(BaseModel):
    total_records: int
    page: int
    size: int
    bookings: List[MerchantBookingResponse]

class BookingStatusUpdate(BaseModel):
    booking_status: BookingStatus
    remarks: Optional[str] = None


class BookingStatusUpdateResponse(BaseModel):
    message: str
    booking_id: str
    old_status: str
    new_status: str