from pydantic import BaseModel, EmailStr, Field
from typing import Literal, Optional, List

class CustomerRegister(BaseModel):
    firstName: str
    lastName: str
    email: EmailStr
    mobileNumber: str = Field(min_length=10, max_length=15)
    password: str = Field(min_length=6, max_length=72)
    confirmPassword: str
    acceptTerms: bool
    acceptPrivacyPolicy: bool
        
class CustomerLogin(BaseModel):
    email: EmailStr
    password: str

# Google login
class GoogleLogin(BaseModel):
    googleToken: Optional[str] = None
    authCode: Optional[str] = None

class CustomerResponse(BaseModel):
    id: str
    firstName: str
    lastName: str
    email: str
    mobileNumber: str

class ForgotPassword(BaseModel):
    email: EmailStr

class ChangePassword(BaseModel):
    currentPassword: str
    newPassword: str
    confirmPassword: str

class ResetPassword(BaseModel):
    resetToken: str
    newPassword: str
    confirmPassword: str

class Address(BaseModel):
    street: str
    city: str
    state: str
    pincode: str

class CustomerProfileResponse(BaseModel):
    email: EmailStr
    firstName: Optional[str]
    lastName: Optional[str]
    mobileNumber: Optional[str]
    profileImage: Optional[str]
    addresses: Optional[List[Address]]

class UpdateCustomerProfile(BaseModel):
    firstName: Optional[str]
    lastName: Optional[str]
    mobileNumber: Optional[str]
    profileImage: Optional[str]
    addresses: Optional[List[Address]]