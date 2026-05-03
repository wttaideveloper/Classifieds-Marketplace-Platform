from pydantic import BaseModel, EmailStr
from typing import Optional, List, Dict

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
        orm_mode = True

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
