from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from uuid import UUID
from datetime import datetime


class BusinessSearchResult(BaseModel):
    business_id: UUID
    business_name: str
    category: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    image_url: Optional[str] = None


class ListingSearchResult(BaseModel):
    listing_id: UUID
    title: str
    price: Optional[float] = None
    business_name: Optional[str] = None
    listing_type: str
    image_url: Optional[str] = None


class NearbyBusinessResult(BaseModel):
    business_id: UUID
    business_name: str
    distance: float
    city: Optional[str] = None
    state: Optional[str] = None
    full_address: Optional[str] = None
    category: Optional[str] = None


class PaginatedResponse(BaseModel):
    page: int
    size: int
    total_elements: int
    total_pages: int
    data: list
