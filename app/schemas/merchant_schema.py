from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, List, Dict
from datetime import datetime, date
from uuid import UUID
from enum import Enum
from enum import Enum as PyEnum

# ENUMS
class ListingType(str, PyEnum):
    PRODUCT = "product"
    SERVICE = "service"
    EVENT = "event"
    TRAINING = "training"
    PROGRAM = "program"

class ListingStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"


class ServiceMode(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    HYBRID = "hybrid"

class BookingStatus(str, PyEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

# FIELD TYPE ENUM
class AttributeFieldType(str, PyEnum):
    TEXT = "text"
    TEXTAREA = "textarea"
    NUMBER = "number"
    DROPDOWN = "dropdown"
    CHECKBOX = "checkbox"
    DATE = "date"

# REGISTER
class MerchantRegister(BaseModel):
    full_name: str
    business_email: EmailStr
    mobile_number: str
    password: str
    confirm_password: str
    business_name: str
    accept_terms: bool
    accept_privacy_policy: bool

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v, values):
        if "password" in values.data and v != values.data["password"]:
            raise ValueError("Passwords do not match")
        return v
# LOGIN
class MerchantLogin(BaseModel):
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

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v, values):
        if "new_password" in values.data and v != values.data["new_password"]:
            raise ValueError("Passwords do not match")
        return v

#  CHANGE PASSWORD
class ChangePassword(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v, values):
        if "new_password" in values.data and v != values.data["new_password"]:
            raise ValueError("Passwords do not match")
        return v

# PROFILE
class MerchantProfileResponse(BaseModel):
    id: UUID
    name: str
    business_email: EmailStr
    mobile_number: str
    profile_image: Optional[str]

    class Config:
        from_attributes = True

class MerchantProfileUpdate(BaseModel):
    name: Optional[str] = None
    mobile_number: Optional[str] = None
    profile_image: Optional[str] = None

class MerchantBusinessProfileCreate(BaseModel):    
    business_name: str    
    business_description: Optional[str] = None   
    primary_category: str    
    subcategory: Optional[str] = None    
    business_email: EmailStr    
    phone_number: str    
    full_address: str    
    city: str    
    state: str    
    zip_code: str    
    country: str    
    latitude: float    
    longitude: float   
    business_logo: Optional[str] = None    
    banner_image: Optional[str] = None    
    gallery_images: List[str] = Field(default_factory=list)  
    operating_hours: Dict = Field(default_factory=dict)   
    business_type: str   # physical | online | hybrid    
    cancellation_policy: Optional[str] = None    
    refund_policy: Optional[str] = None    
    merchant_terms_of_service: Optional[str] = None    
    website_url: Optional[str] = None    
    social_media_links: Dict = Field(default_factory=dict)   
    additional_contact_numbers: List[str] = Field(default_factory=list)    
    short_tagline: Optional[str] = None

class MerchantBusinessDraft(BaseModel):
    business_name: Optional[str] = None
    business_description: Optional[str] = None
    primary_category: Optional[str] = None
    subcategory: Optional[str] = None
    business_email: Optional[EmailStr] = None
    phone_number: Optional[str] = None
    full_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    country: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[str] = None
    business_logo: Optional[str] = None
    banner_image: Optional[str] = None
    gallery_images: List[str] = Field(default_factory=list)
    operating_hours: Dict = Field(default_factory=dict)
    business_type: Optional[str] = None
    cancellation_policy: Optional[str] = None
    refund_policy: Optional[str] = None
    merchant_terms_of_service: Optional[str] = None
    website_url: Optional[str] = None
    social_media_links: Dict = Field(default_factory=dict)
    additional_contact_numbers: List[str] = Field(default_factory=list)
    short_tagline: Optional[str] = None

class MerchantBusinessProfileData(BaseModel):
    id: UUID
    merchant_id: UUID
    business_name: str
    business_description: Optional[str]
    primary_category: Optional[str]
    subcategory: Optional[str]
    business_email: Optional[str]
    phone_number: Optional[str]
    full_address: Optional[str]
    city: Optional[str]
    state: Optional[str]
    zip_code: Optional[str]
    country: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    business_logo: Optional[str]
    banner_image: Optional[str]
    gallery_images: List[str] = Field(default_factory=list)
    operating_hours: Dict = Field(default_factory=dict)
    business_type: Optional[str]
    cancellation_policy: Optional[str]
    refund_policy: Optional[str]
    merchant_terms_of_service: Optional[str]
    website_url: Optional[str]
    social_media_links: Dict = Field(default_factory=dict)
    additional_contact_numbers: List[str] = Field(default_factory=list)
    short_tagline: Optional[str]

    class Config:
        from_attributes = True

class MerchantBusinessProfileResponse(BaseModel):
    success: bool
    message: str
    data: MerchantBusinessProfileData

class BusinessStatusResponse(BaseModel):
    success: bool
    message: str
    business_status: str
    business_name: Optional[str] = None



class UpdateBusinessProfile(BaseModel):
    # NORMAL EDITABLE FIELDS
    # Description
    business_description: Optional[str] = None
    # Images
    business_logo: Optional[str] = None
    banner_image: Optional[str] = None
    gallery_images: List[str] = Field(default_factory=list)
    # Contact Details
    business_email: Optional[EmailStr] = None
    phone_number: Optional[str] = None
    additional_contact_numbers: Optional[List[str]] = None
    website_url: Optional[str] = None
    social_media_links: Dict = Field(default_factory=dict)
    # Operating Hours
    operating_hours: Dict = Field(default_factory=dict)
    # Policies
    cancellation_policy: Optional[str] = None
    refund_policy: Optional[str] = None
    merchant_terms_of_service: Optional[str] = None
    # Optional branding text
    short_tagline: Optional[str] = None
    # RESTRICTED FIELDS (May Need Approval)
    # Business Name
    business_name: Optional[str] = None
    # Address / Location
    full_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    country: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    # Category
    primary_category: Optional[str] = None
    subcategory: Optional[str] = None

class MerchantListingBase(BaseModel):

    business_id: UUID
    listing_type: ListingType
    title: str
    description: str
    category_id: UUID
    subcategory_id: Optional[UUID] = None
    price: float
    currency: str = "INR"
    images: List[str] = Field(default_factory=list)
    status: ListingStatus = ListingStatus.DRAFT
    tags: List[str] = Field(default_factory=list)
    # PRODUCT
    stock_quantity: Optional[int] = None
    sku: Optional[str] = None
    weight: Optional[float] = None
    # SERVICE
    duration: Optional[str] = None
    service_mode: Optional[ServiceMode] = None
    availability: Optional[str] = None
    schedule: Optional[str] = None
    # EVENT / TRAINING / PROGRAM
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    capacity: Optional[int] = None
    location: Optional[str] = None
    is_online: bool = False
    registration_deadline: Optional[datetime] = None

class MerchantListingCreate(MerchantListingBase):
    pass

class MerchantListingCreateResponse(MerchantListingBase):

    id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class MerchantDraftCreate(BaseModel):

    business_id: Optional[UUID] = None
    listing_type: Optional[ListingType] = None
    title: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[UUID] = None
    subcategory_id: Optional[UUID] = None
    price: Optional[float] = None
    currency: str = "INR"
    images: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    stock_quantity: Optional[int] = None
    sku: Optional[str] = None
    weight: Optional[float] = None
    duration: Optional[str] = None
    service_mode: Optional[ServiceMode] = None
    availability: Optional[str] = None
    schedule: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    capacity: Optional[int] = None
    location: Optional[str] = None
    is_online: bool = False
    registration_deadline: Optional[datetime] = None

class MerchantDraftResponse(BaseModel):

    id: UUID
    business_id: Optional[UUID]
    listing_type: Optional[ListingType]
    title: Optional[str]
    description: Optional[str]
    status: ListingStatus

    class Config:
        from_attributes = True

class MerchantListingListResponse(BaseModel):

    id: UUID
    business_id: UUID
    listing_type: ListingType
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
    business_id: UUID
    listing_type: ListingType
    title: str
    description: Optional[str]
    category_id: Optional[UUID]
    subcategory_id: Optional[UUID]
    price: Optional[float]
    currency: Optional[str]
    images: List[str]
    status: ListingStatus
    tags: List[str]
    stock_quantity: Optional[int]
    sku: Optional[str]
    weight: Optional[float]
    duration: Optional[str]
    service_mode: Optional[ServiceMode]
    availability: Optional[str]
    schedule: Optional[str]
    start_date: Optional[datetime]
    end_date: Optional[datetime]
    capacity: Optional[int]
    location: Optional[str]
    is_online: bool
    registration_deadline: Optional[datetime]
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
    deleted_listing_id: UUID

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

    listing_id: UUID
    images: List[str]

class DeleteListingImageResponse(BaseModel):

    success: bool
    message: str
    data: dict

class DeleteListingImageData(BaseModel):

    listing_id: UUID
    deleted_image: str
    remaining_images: List[str]

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

# ATTRIBUTE OPTION RESPONSE
class AttributeOptionResponse(BaseModel):

    id: UUID
    option_label: str
    option_value: str

    class Config:
        from_attributes = True

# MERCHANT ATTRIBUTE RESPONSE
class MerchantAttributeListResponse(BaseModel):

    id: UUID
    merchant_id: UUID
    attribute_id: UUID
    name: str
    display_name: str
    slug: str
    field_type: AttributeFieldType
    custom_label: Optional[str]
    custom_placeholder: Optional[str]
    is_required: bool
    default_value: Optional[str]
    is_active: bool
    created_at: datetime
    options: List[AttributeOptionResponse] = []

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