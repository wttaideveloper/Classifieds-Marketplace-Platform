from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Dict
from datetime import datetime
from uuid import UUID

# REGISTER
class MerchantRegister(BaseModel):
    fullName: str
    businessEmail: EmailStr
    mobileNumber: str
    password: str
    confirmPassword: str
    businessName: str
    acceptTerms: bool
    acceptPrivacyPolicy: bool

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
    resetToken: str
    newPassword: str
    confirmPassword: str


#  CHANGE PASSWORD
class ChangePassword(BaseModel):
    currentPassword: str
    newPassword: str
    confirmPassword: str

# PROFILE
class MerchantProfileUpdate(BaseModel):
    name: Optional[str] = None
    mobileNumber: Optional[str] = None
    profileImage: Optional[str] = None

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
    latitude: str    
    longitude: str    
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

    # Common Fields
    businessId: UUID
    listingType: str = Field(..., pattern="^(product|service|event|training|program)$")
    title: str
    description: str
    categoryId: UUID
    subcategoryId: Optional[UUID] = None
    price: float
    currency: str = "INR"
    images: Optional[List[str]] = []
    status: str = Field(default="draft", pattern="^(draft|published)$")
    tags: Optional[List[str]] = []
    # Product Fields
    stockQuantity: Optional[int] = None
    sku: Optional[str] = None
    weight: Optional[float] = None
    # Service Fields
    duration: Optional[str] = None
    serviceMode: Optional[str] = Field(
        default=None,
        pattern="^(online|offline|hybrid)$"
    )
    availability: Optional[str] = None
    # Event / Training / Program Fields
    startDate: Optional[datetime] = None
    endDate: Optional[datetime] = None
    capacity: Optional[int] = None
    location: Optional[str] = None
    isOnline: Optional[bool] = False
    registrationDeadline: Optional[datetime] = None

class MerchantListingCreate(MerchantListingBase):
    pass

class MerchantListingResponse(MerchantListingBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class MerchantDraftCreate(BaseModel):

    # Common Fields
    businessId: Optional[UUID] = None
    listingType: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    categoryId: Optional[UUID] = None
    subcategoryId: Optional[UUID] = None
    price: Optional[float] = None
    currency: Optional[str] = "INR"
    images: Optional[List[str]] = []
    tags: Optional[List[str]] = []
    # Product Fields
    stockQuantity: Optional[int] = None
    sku: Optional[str] = None
    weight: Optional[float] = None
    # Service Fields
    duration: Optional[str] = None
    serviceMode: Optional[str] = None
    availability: Optional[str] = None
    # Event / Training / Program Fields
    startDate: Optional[datetime] = None
    endDate: Optional[datetime] = None
    capacity: Optional[int] = None
    location: Optional[str] = None
    isOnline: Optional[bool] = False
    registrationDeadline: Optional[datetime] = None

class MerchantDraftResponse(BaseModel):

    id: UUID
    businessId: Optional[UUID]
    listingType: Optional[str]
    title: Optional[str]
    description: Optional[str]
    status: str

    class Config:
        from_attributes = True

class MerchantListingResponse(BaseModel):

    id: UUID
    businessId: UUID
    listingType: str
    title: str
    description: Optional[str]
    price: Optional[float]
    currency: Optional[str]
    images: Optional[List[str]]
    status: str
    tags: Optional[List[str]]
    created_at: datetime

    class Config:
        from_attributes = True

class MerchantListingPaginationResponse(BaseModel):

    success: bool
    message: str
    total: int
    page: int
    limit: int
    data: List[MerchantListingResponse]

class MerchantListingDetailsResponse(BaseModel):

    id: UUID
    businessId: UUID
    listingType: str
    title: str
    description: Optional[str]
    categoryId: Optional[UUID]
    subcategoryId: Optional[UUID]
    price: Optional[float]
    currency: Optional[str]
    images: Optional[List[str]]
    status: str
    tags: Optional[List[str]]
    # Product Fields
    stockQuantity: Optional[int]
    sku: Optional[str]
    weight: Optional[float]
    # Service Fields
    duration: Optional[str]
    serviceMode: Optional[str]
    availability: Optional[str]
    # Event / Training / Program Fields
    startDate: Optional[datetime]
    endDate: Optional[datetime]
    capacity: Optional[int]
    location: Optional[str]
    isOnline: Optional[bool]
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