from uuid import UUID

from pydantic import BaseModel
from pydantic import EmailStr

from typing import Optional


class EnterpriseCreate(BaseModel):
    business_short_name: str
    business_legal_name: str
    business_description: Optional[str] = None
    business_email: EmailStr
    business_phone: Optional[str] = None
    registered_address: Optional[str] = None
    business_address: Optional[str] = None
    communication_address: Optional[str] = None
    logo_url: Optional[str] = None
    business_images: Optional[str] = None


class EnterpriseUpdate(BaseModel):
    business_short_name: Optional[str] = None
    business_legal_name: Optional[str] = None
    business_description: Optional[str] = None
    business_email: Optional[EmailStr] = None
    business_phone: Optional[str] = None
    registered_address: Optional[str] = None
    business_address: Optional[str] = None
    communication_address: Optional[str] = None
    logo_url: Optional[str] = None
    business_images: Optional[str] = None
    status: Optional[bool] = None


class EnterpriseResponse(BaseModel):
    id: UUID
    business_short_name: str
    business_legal_name: str
    business_description: Optional[str]
    business_email: str
    business_phone: Optional[str]
    registered_address: Optional[str]
    business_address: Optional[str]
    communication_address: Optional[str]
    logo_url: Optional[str]
    business_images: Optional[str]
    status: bool

    class Config:
        from_attributes = True