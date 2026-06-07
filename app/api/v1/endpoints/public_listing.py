from fastapi import APIRouter, Depends, Query, status, UploadFile, File
from typing import List
from sqlalchemy.orm import Session
from app.schemas.common_schema import (
    CategoryListResponse,
    SubCategoryListResponse,
    UploadListingImagesResponse,
    CreateBooking,
    CreateBookingResponse
)
from app.db.database import get_db
from app.utils.common import (
    generate_booking_number
)
from app.db.database import get_db
from app.repository.customer_repo import create_booking_repo
from app.services.customer_service import (
    get_public_listings_service,
    get_public_listing_details_service,
    search_listings_service,
    get_categories_service,
    get_subcategories_service,
    create_booking_service
)
from app.services.common_service import (
    validate_role,
    upload_business_image_service,
    upload_listing_images_service
)
from uuid import UUID

router = APIRouter()

@router.get(
    "/listings",
    status_code=status.HTTP_200_OK
)
def get_public_listings(

    search: str = None,
    category: str = None,
    listing_type: str = None,
    city: str = None,
    price_min: float = None,
    price_max: float = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1),
    sort_by: str = "latest",
    db: Session = Depends(get_db)
):
    return get_public_listings_service(
        db=db,
        search=search,
        category=category,
        listing_type=listing_type,
        city=city,
        price_min=price_min,
        price_max=price_max,
        page=page,
        limit=limit,
        sort_by=sort_by
    )

# SEARCH LISTINGS
@router.get(
    "/listings/search",
    status_code=status.HTTP_200_OK
)
def search_listings(

    role: str,
    keyword: str = Query(default=None),
    category: str = Query(default=None),
    listing_type: str = Query(default=None),
    location: str = Query(default=None),
    rating: float = Query(default=None),
    sort: str = Query(default=None),
    db: Session = Depends(get_db)
):

    validate_role(role)

    return search_listings_service(
        db=db,
        keyword=keyword,
        category=category,
        listingType=listing_type,
        location=location,
        rating=rating,
        sort=sort
    )

@router.get(
    "/listings/{listing_id}",
    status_code=status.HTTP_200_OK
)
def get_listing_details(
    listing_id: UUID,
    db: Session = Depends(get_db)
):
    return get_public_listing_details_service(
        db=db,
        listing_id=listing_id
    )

# GET CATEGORIES
@router.get(
    "/categories",
    response_model=CategoryListResponse,
    status_code=status.HTTP_200_OK
)
def get_categories(
    db: Session = Depends(get_db)
):
    return get_categories_service(db)

# GET SUBCATEGORIES
@router.get(
    "/categories/{category_id}/subcategories",
    response_model=SubCategoryListResponse,
    status_code=status.HTTP_200_OK
)
def get_subcategories(
    category_id: UUID,
    db: Session = Depends(get_db)
):

    return get_subcategories_service(
        db=db,
        category_id=category_id
    )

@router.post(
    "/upload/business-image",
    status_code=status.HTTP_201_CREATED
)
async def upload_business_image(
    files: List[UploadFile] = File(...)
):
    return await upload_business_image_service(
        files
    )

@router.post(
    "/upload/listing-images",
    response_model=UploadListingImagesResponse,
    status_code=status.HTTP_201_CREATED
)
async def upload_listing_images(
    files: List[UploadFile] = File(...)
):

    return await upload_listing_images_service(
        files
    )

@router.post(
    "/bookings",
    response_model=CreateBookingResponse,
    status_code=status.HTTP_201_CREATED
)
def create_booking(
    payload: CreateBooking,
    db: Session = Depends(get_db)
):

    return create_booking_service(
        db=db,
        payload=payload
    )
