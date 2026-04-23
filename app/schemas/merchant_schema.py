from pydantic import BaseModel, EmailStr
from typing import Optional

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
