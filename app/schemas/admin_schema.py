# app/schemas/admin_schema.py

from pydantic import BaseModel, EmailStr

class AdminLogin(BaseModel):
    email: EmailStr
    password: str


class ForgotPassword(BaseModel):
    email: EmailStr
    role: str


class ResetPassword(BaseModel):
    resetToken: str
    newPassword: str
    confirmPassword: str


class ChangePassword(BaseModel):
    currentPassword: str
    newPassword: str
    confirmPassword: str

class AdminProfileUpdate(BaseModel):
    name: str
    email: EmailStr