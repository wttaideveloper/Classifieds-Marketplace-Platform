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
    firstName: Optional[str]
    lastName: Optional[str]
    mobileNumber: Optional[str]
    profileImage: Optional[str]
    addresses: Optional[str]

# ADDRESS
class AddressBase(BaseModel):
    addressLine1: str
    addressLine2: Optional[str]
    city: str
    state: str
    zipCode: str
    country: str
    isDefault: bool = False