from pydantic import BaseModel, model_validator, Field
from datetime import datetime
from uuid import UUID
from typing import Optional, List
from enum import Enum as PyEnum


class WishlistType(str, PyEnum):
    Business = "business"
    Listing = "listing"


class WishlistCreate(BaseModel):
    customer_id: UUID
    wishlist_type: WishlistType
    business_id: Optional[UUID] = None
    listing_id: Optional[UUID] = None

    @model_validator(mode="after")
    def validate_wishlist(self):

        if self.wishlist_type == WishlistType.Business:
            if not self.business_id:
                raise ValueError(
                    "business_id is required for business wishlist"
                )

        if self.wishlist_type == WishlistType.Listing:
            if not self.listing_id:
                raise ValueError(
                    "listing_id is required for listing wishlist"
                )

        return self


class WishlistDataResponse(BaseModel):
    wishlist_id: UUID = Field(alias="id")
    wishlist_type: WishlistType
    created_at: datetime

    class Config:
        from_attributes = True


class WishlistResponse(BaseModel):
    success: bool
    message: str
    data: WishlistDataResponse

class WishlistItemResponse(BaseModel):
    wishlist_id: UUID = Field(alias="id")
    customer_id: UUID
    business_id: Optional[UUID] = None
    listing_id: Optional[UUID] = None
    wishlist_type: WishlistType
    created_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True


class WishlistListResponse(BaseModel):
    success: bool
    message: str
    data: List[WishlistItemResponse]


class WishlistDeleteResponse(BaseModel):
    success: bool
    message: str


class BusinessWishlistResponse(BaseModel):
    wishlist_id: UUID = Field(alias="id")
    customer_id: UUID
    business_id: UUID
    wishlist_type: WishlistType
    created_at: datetime

    class Config:
        from_attributes = True


class BusinessWishlistListResponse(BaseModel):
    success: bool
    message: str
    data: List[BusinessWishlistResponse]


class ListingWishlistResponse(BaseModel):
    wishlist_id: UUID = Field(alias="id")
    customer_id: UUID
    listing_id: UUID
    wishlist_type: WishlistType
    created_at: datetime

    class Config:
        from_attributes = True


class ListingWishlistListResponse(BaseModel):
    success: bool
    message: str
    total_records: int
    page: int
    size: int
    data: List[ListingWishlistResponse]