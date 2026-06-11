from pydantic import BaseModel, EmailStr
from uuid import UUID
from datetime import datetime
from typing import Optional


class EnterpriseSetupCreate(BaseModel):
    organization_name: str
    organization_code: str
    industry: Optional[str] = None
    website: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    company_size: Optional[str] = None


class EnterpriseSetupUpdate(BaseModel):
    organization_name: Optional[str] = None
    industry: Optional[str] = None
    website: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    company_size: Optional[str] = None


class EnterpriseSetupResponse(BaseModel):
    enterprise_id: UUID
    organization_name: str
    organization_code: str
    industry: Optional[str]
    website: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    address: Optional[str]
    company_size: Optional[str]

    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True