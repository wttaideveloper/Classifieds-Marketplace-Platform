from pydantic import BaseModel, EmailStr

class RefreshTokenSchema(BaseModel):
    refreshToken: str

class VerifyEmailSchema(BaseModel):
    verificationToken: str

class ResendVerificationSchema(BaseModel):
    email: EmailStr
