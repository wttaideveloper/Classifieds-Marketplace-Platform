from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl


class SeoEntityType(str, Enum):
    Business = "Business"
    Listing = "Listing"
    Blog = "Blog"


class ChangeFrequency(str, Enum):
    Daily = "Daily"
    Weekly = "Weekly"
    Monthly = "Monthly"


class SeoMetaCreate(BaseModel):
    entity_type: SeoEntityType
    entity_id: str
    meta_title: str = Field(..., min_length=1, max_length=255)
    meta_description: str = Field(..., min_length=1)
    slug: str = Field(..., min_length=1, max_length=255)
    meta_keywords: Optional[str] = None
    canonical_url: Optional[HttpUrl] = None
    og_title: Optional[str] = Field(None, max_length=255)
    og_description: Optional[str] = None
    og_image: Optional[HttpUrl] = None


class SeoMetaUpdate(BaseModel):
    meta_title: Optional[str] = Field(None, min_length=1, max_length=255)
    meta_description: Optional[str] = Field(None, min_length=1)
    meta_keywords: Optional[str] = None
    canonical_url: Optional[HttpUrl] = None
    og_title: Optional[str] = Field(None, max_length=255)
    og_description: Optional[str] = None
    og_image: Optional[HttpUrl] = None
    slug: Optional[str] = Field(None, min_length=1, max_length=255)


class CanonicalUrlCreate(BaseModel):
    entity_type: SeoEntityType
    entity_id: str
    canonical_url: HttpUrl


class SeoMetaResponse(BaseModel):
    seo_id: str
    entity_type: SeoEntityType
    entity_id: str
    meta_title: str
    meta_description: str
    meta_keywords: Optional[str] = None
    canonical_url: Optional[str] = None
    og_title: Optional[str] = None
    og_description: Optional[str] = None
    og_image: Optional[str] = None
    slug: str
    sitemap_url: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SeoApiResponse(BaseModel):
    success: bool
    message: str
    data: SeoMetaResponse
