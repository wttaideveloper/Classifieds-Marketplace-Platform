from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from app.schemas.common_schema import (
    CategoryListResponse,
    SubCategoryListResponse
)
from app.db.database import SessionLocal
from app.services.common_service import validate_role
from app.services.customer_service import (
    get_public_listings_service,
    get_public_listing_details_service,
    search_listings_service,
    get_categories_service,
    get_subcategories_service
)
from uuid import UUID

router = APIRouter()

def get_db():

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get(
    "/listings",
    status_code=status.HTTP_200_OK
)
def get_public_listings(

    search: str = None,
    category: str = None,
    listingType: str = None,
    city: str = None,
    priceMin: float = None,
    priceMax: float = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1),
    sortBy: str = "latest",
    db: Session = Depends(get_db)
):
    return get_public_listings_service(
        db=db,
        search=search,
        category=category,
        listingType=listingType,
        city=city,
        priceMin=priceMin,
        priceMax=priceMax,
        page=page,
        limit=limit,
        sortBy=sortBy
    )

@router.get(
    "/listings/{listingId}",
    status_code=status.HTTP_200_OK
)
def get_listing_details(
    listingId: str,
    db: Session = Depends(get_db)
):
    return get_public_listing_details_service(
        db=db,
        listingId=listingId
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
    listingType: str = Query(default=None),
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
        listingType=listingType,
        location=location,
        rating=rating,
        sort=sort
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
    "/categories/{categoryId}/subcategories",
    response_model=SubCategoryListResponse,
    status_code=status.HTTP_200_OK
)
def get_subcategories(
    categoryId: UUID,
    db: Session = Depends(get_db)
):

    return get_subcategories_service(
        db=db,
        categoryId=categoryId
    )