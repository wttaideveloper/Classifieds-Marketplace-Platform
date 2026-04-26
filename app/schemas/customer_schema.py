from pydantic import BaseModel, EmailStr
from typing import Optional, List

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