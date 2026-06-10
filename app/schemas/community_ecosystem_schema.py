from pydantic import BaseModel, EmailStr
from uuid import UUID
from datetime import datetime
from typing import Optional


class CommunityEcosystemCreate(BaseModel):
    provider_name: str
    provider_type: str
    description: Optional[str] = None
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = None
    address: Optional[str] = None
    website: Optional[str] = None


class CommunityEcosystemUpdate(BaseModel):
    provider_name: Optional[str] = None
    provider_type: Optional[str] = None
    description: Optional[str] = None
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = None
    address: Optional[str] = None
    website: Optional[str] = None


class CommunityEcosystemResponse(BaseModel):
    ecosystem_id: UUID
    provider_name: str
    provider_type: str
    description: Optional[str]
    contact_email: Optional[str]
    contact_phone: Optional[str]
    address: Optional[str]
    website: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True