from fastapi import APIRouter, Depends, Query, status, UploadFile, File
from typing import List
from sqlalchemy.orm import Session
from app.schemas.common_schema import (
    CategoryListResponse,
    SubCategoryListResponse,
    UploadListingImagesResponse,
    CreateBooking,
    CreateBookingResponse,
    EnterpriseCreatePayload,
    EnterpriseUpdatePayload,
    EnterpriseListResponse,
    EnterpriseDetailsResponse,
    ProductCreatePayload,
    ProductUpdatePayload,
    ProductListResponse,
    ProductDetailsResponse,
    ServiceCreatePayload,
    ServiceUpdatePayload,
    ServiceListResponse,
    ServiceDetailsResponse
)
from app.utils.common import (
    generate_booking_number
)
from app.db.database import get_db
from app.repository.customer_repo import create_booking_repo
from app.services.customer_service import (
    get_public_listings_service,
    get_public_listing_details_service,
    create_enterprise_service,
    update_enterprise_service,
    get_enterprises_service,
    get_enterprise_details_service,
    create_product_service,
    update_product_service,
    get_products_service,
    get_product_details_service,
    create_service_service,
    update_service_service,
    get_services_service,
    get_service_details_service,
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


@router.post(
    "/api/enterprises/",
    response_model=EnterpriseDetailsResponse,
    status_code=status.HTTP_201_CREATED
)
def create_enterprise(
    payload: EnterpriseCreatePayload,
    db: Session = Depends(get_db)
):
    return create_enterprise_service(
        db=db,
        payload=payload
    )


@router.get(
    "/api/enterprises/",
    response_model=EnterpriseListResponse,
    status_code=status.HTTP_200_OK
)
def get_enterprises(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1),
    db: Session = Depends(get_db)
):
    return get_enterprises_service(
        db=db,
        page=page,
        limit=limit
    )


@router.put(
    "/api/enterprises/{enterprise_id}",
    response_model=EnterpriseDetailsResponse,
    status_code=status.HTTP_200_OK
)
def update_enterprise(
    enterprise_id: UUID,
    payload: EnterpriseUpdatePayload,
    db: Session = Depends(get_db)
):
    return update_enterprise_service(
        db=db,
        enterprise_id=enterprise_id,
        payload=payload
    )


@router.get(
    "/api/enterprises/{enterprise_id}",
    response_model=EnterpriseDetailsResponse,
    status_code=status.HTTP_200_OK
)
def get_enterprise_details(
    enterprise_id: UUID,
    db: Session = Depends(get_db)
):
    return get_enterprise_details_service(
        db=db,
        enterprise_id=enterprise_id
    )


@router.post(
    "/api/products/",
    response_model=ProductDetailsResponse,
    status_code=status.HTTP_201_CREATED
)
def create_product(
    payload: ProductCreatePayload,
    db: Session = Depends(get_db)
):
    return create_product_service(
        db=db,
        payload=payload
    )


@router.get(
    "/api/products/",
    response_model=ProductListResponse,
    status_code=status.HTTP_200_OK
)
def get_products(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1),
    db: Session = Depends(get_db)
):
    return get_products_service(
        db=db,
        page=page,
        limit=limit
    )


@router.put(
    "/api/products/{product_id}",
    response_model=ProductDetailsResponse,
    status_code=status.HTTP_200_OK
)
def update_product(
    product_id: UUID,
    payload: ProductUpdatePayload,
    db: Session = Depends(get_db)
):
    return update_product_service(
        db=db,
        product_id=product_id,
        payload=payload
    )


@router.get(
    "/api/products/{product_id}",
    response_model=ProductDetailsResponse,
    status_code=status.HTTP_200_OK
)
def get_product_details(
    product_id: UUID,
    db: Session = Depends(get_db)
):
    return get_product_details_service(
        db=db,
        product_id=product_id
    )


@router.post(
    "/api/services/",
    response_model=ServiceDetailsResponse,
    status_code=status.HTTP_201_CREATED
)
def create_service(
    payload: ServiceCreatePayload,
    db: Session = Depends(get_db)
):
    return create_service_service(
        db=db,
        payload=payload
    )


@router.get(
    "/api/services/",
    response_model=ServiceListResponse,
    status_code=status.HTTP_200_OK
)
def get_services(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1),
    db: Session = Depends(get_db)
):
    return get_services_service(
        db=db,
        page=page,
        limit=limit
    )


@router.put(
    "/api/services/{service_id}",
    response_model=ServiceDetailsResponse,
    status_code=status.HTTP_200_OK
)
def update_service(
    service_id: UUID,
    payload: ServiceUpdatePayload,
    db: Session = Depends(get_db)
):
    return update_service_service(
        db=db,
        service_id=service_id,
        payload=payload
    )


@router.get(
    "/api/services/{service_id}",
    response_model=ServiceDetailsResponse,
    status_code=status.HTTP_200_OK
)
def get_service_details(
    service_id: UUID,
    db: Session = Depends(get_db)
):
    return get_service_details_service(
        db=db,
        service_id=service_id
    )

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
        listingType=listing_type,
        city=city,
        priceMin=price_min,
        priceMax=price_max,
        page=page,
        limit=limit,
        sortBy=sort_by
    )

@router.get(
    "/listings/{listing_id}",
    status_code=status.HTTP_200_OK
)
def get_listing_details(
    listing_id: str,
    db: Session = Depends(get_db)
):
    return get_public_listing_details_service(
        db=db,
        listingId=listing_id
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
        categoryId=category_id
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
