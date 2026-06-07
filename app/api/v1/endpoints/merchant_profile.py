from fastapi import APIRouter, Depends, status, UploadFile, File, Query
from typing import List, Optional
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.core.dependencies import get_current_user
from datetime import date
from app.services.merchant_service import (
    get_merchant_profile_service, 
    update_merchant_profile_service, 
    create_business_profile_service, 
    save_business_draft_service, 
    get_business_profile_service,
    update_business_profile_service,
    submit_business_for_approval_service,
    upload_business_logo_service,
    upload_business_banner_service,
    upload_business_gallery_service,
    delete_business_gallery_image_service,
    get_business_status_service,
    create_listing_service,
    save_listing_draft_service,
    get_my_listings_service,
    get_listing_details_service,
    update_listing_service,
    delete_listing_service,
    publish_listing_service,
    unpublish_listing_service,
    upload_listing_images_service,
    delete_listing_image_service,
    create_custom_attribute_service,
    map_attribute_to_business_service,
    map_attribute_to_listing_service,
    get_merchant_bookings_service,
    update_booking_status_service,
    get_merchant_attributes_service
)
from app.schemas.merchant_schema import (
    MerchantProfileResponse,
    MerchantProfileUpdate, 
    MerchantBusinessProfileCreate, 
    MerchantBusinessProfileResponse,
    MerchantBusinessDraft, 
    UpdateBusinessProfile,
    BusinessStatusResponse,
    MerchantListingCreate,
    MerchantListingCreateResponse,
    MerchantDraftCreate,
    MerchantDraftResponse,
    MerchantListingPaginationResponse,
    MerchantListingDetailsResponse,
    UpdateMerchantListing, 
    MerchantListingUpdateResponse,
    DeleteMerchantListingResponse,
    PublishListingResponse,
    UnpublishListingResponse,
    UploadListingImagesResponse,
    DeleteListingImageResponse,
    MerchantCustomAttributeCreate,
    MerchantCustomAttributeResponse,
    MerchantAttributeListResponse,
    BusinessAttributeMapCreate,
    BusinessAttributeMapResponse,
    ListingAttributeMapCreate,
    ListingAttributeMapResponse,
    MerchantBookingList,
    BookingStatusUpdateResponse,
    BookingStatusUpdate
)
from app.db.database import get_db
from uuid import UUID

router = APIRouter(
    tags=["Merchant"]
)

@router.get("/profile", response_model=MerchantProfileResponse, status_code=status.HTTP_200_OK)
def get_profile(
    merchant_id: UUID = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return get_merchant_profile_service(db, merchant_id)

# UPDATE PROFILE API
@router.put("/profile", response_model=MerchantProfileResponse, status_code=status.HTTP_200_OK)
def update_profile(
    payload: MerchantProfileUpdate,
    merchant_id: UUID = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return update_merchant_profile_service(
        db,
        merchant_id,
        payload.model_dump()
    )

@router.post("/business", response_model=MerchantBusinessProfileResponse, status_code=status.HTTP_201_CREATED)
def create_business_profile(
    payload: MerchantBusinessProfileCreate,
    merchant_id: UUID = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return create_business_profile_service(
        db,
        merchant_id,
        payload
    )

@router.post(
    "/business/draft",
    status_code=status.HTTP_200_OK
)
def save_business_draft(
    payload: MerchantBusinessDraft,
    merchant_id: UUID = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return save_business_draft_service(
        db,
        merchant_id,
        payload
    )

@router.get(
    "/business",
    response_model=MerchantBusinessProfileResponse,
    status_code=status.HTTP_200_OK
)
def get_my_business_profile(
    merchant_id: UUID = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return get_business_profile_service(
        db,
        merchant_id
    )

@router.put(
    "/business",
    response_model=MerchantBusinessProfileResponse,
    status_code=status.HTTP_200_OK
)
def update_business_profile(
    payload: UpdateBusinessProfile,
    merchant_id: UUID = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return update_business_profile_service(
        db,
        merchant_id,
        payload
    )

@router.post(
    "/business/submit",
    status_code=status.HTTP_200_OK
)
def submit_business_for_approval(
    merchant_id: UUID = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return submit_business_for_approval_service(
        db,
        merchant_id
    )

@router.post(
    "/business/logo",
    status_code=status.HTTP_200_OK
)
def upload_business_logo(
    file: UploadFile = File(...),
    merchant_id: UUID = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return upload_business_logo_service(
        db,
        merchant_id,
        file
    )

@router.post(
    "/business/banner",
    status_code=status.HTTP_200_OK
)
def upload_business_banner(
    file: UploadFile = File(...),
    merchant_id: UUID = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return upload_business_banner_service(
        db,
        merchant_id,
        file
    )

@router.post(
    "/business/gallery",
    status_code=status.HTTP_200_OK
)
def upload_business_gallery(
    files: List[UploadFile] = File(...),
    merchant_id: UUID = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return upload_business_gallery_service(
        db,
        merchant_id,
        files
    )

@router.delete(
    "/business/gallery/{image_id}",
    status_code=status.HTTP_200_OK
)
def delete_business_gallery_image(
    image_id: str,
    merchant_id: UUID = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return delete_business_gallery_image_service(
        db,
        merchant_id,
        image_id
    )

@router.get(
    "/business/status",
    response_model=BusinessStatusResponse,
    status_code=status.HTTP_200_OK
)
def get_business_status(
    merchant_id: UUID = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return get_business_status_service(
        db,
        merchant_id
    )

# CREATE LISTING
@router.post(
    "/listings",
    response_model=MerchantListingCreateResponse,
    status_code=status.HTTP_201_CREATED
)
def create_listing(
    payload: MerchantListingCreate,
    merchant_id: UUID = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return create_listing_service(
        db=db,
        merchant_id=merchant_id,
        payload=payload
    )

# SAVE LISTING AS DRAFT
@router.post(
    "/listings/draft",
    response_model=MerchantDraftResponse,
    status_code=status.HTTP_201_CREATED
)
def save_listing_as_draft(
    payload: MerchantDraftCreate,
    merchant_id: UUID = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return save_listing_draft_service(
        db=db,
        merchant_id=merchant_id,
        payload=payload
    )

# GET MY LISTINGS
@router.get(
    "/listings",
    response_model=MerchantListingPaginationResponse,
    status_code=status.HTTP_200_OK
)
def get_my_listings(
    merchant_id: UUID = Query(..., description="Merchant id"),
    business_id: Optional[UUID] = None,
    status_filter: str = Query(
        default=None,
        alias="status"
    ),
    listing_type: str = None,
    search: str = None,
    page: int = 1,
    limit: int = 10,
    db: Session = Depends(get_db)
):
    return get_my_listings_service(
        db=db,
        merchant_id=merchant_id,
        business_id=business_id,
        status_filter=status_filter,
        listing_type=listing_type,
        search=search,
        page=page,
        limit=limit
    )

# GET LISTING DETAILS
@router.get(
    "/listings/{listing_id}",
    response_model=MerchantListingDetailsResponse,
    status_code=status.HTTP_200_OK
)
def get_listing_details(
    listing_id: UUID,
    db: Session = Depends(get_db)
):
    return get_listing_details_service(
        db=db,
        listing_id=listing_id
    )

# UPDATE LISTING
@router.put(
    "/listings/{listing_id}",
    response_model=MerchantListingUpdateResponse,
    status_code=status.HTTP_200_OK
)
def update_listing(
    listing_id: UUID,
    payload: UpdateMerchantListing,
    merchant_id: UUID = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return update_listing_service(
        db=db,
        merchant_id=merchant_id,
        listing_id=listing_id,
        payload=payload
    )

# DELETE LISTING
@router.delete(
    "/listings/{listing_id}",
    response_model=DeleteMerchantListingResponse,
    status_code=status.HTTP_200_OK
)
def delete_listing(
    listing_id: UUID,
    merchant_id: UUID = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return delete_listing_service(
        db=db,
        merchant_id=merchant_id,
        listing_id=listing_id
    )

# PUBLISH LISTING
@router.patch(
    "/listings/{listing_id}/publish",
    response_model=PublishListingResponse,
    status_code=status.HTTP_200_OK
)
def publish_listing(
    listing_id: UUID,
    merchant_id: UUID = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return publish_listing_service(
        db=db,
        merchant_id=merchant_id,
        listing_id=listing_id
    )

# UNPUBLISH LISTING
@router.patch(
    "/listings/{listing_id}/unpublish",
    response_model=UnpublishListingResponse,
    status_code=status.HTTP_200_OK
)
def unpublish_listing(
    listing_id: UUID,
    merchant_id: UUID = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return unpublish_listing_service(
        db=db,
        merchant_id=merchant_id,
        listing_id=listing_id
    )

# UPLOAD LISTING IMAGES
@router.post(
    "/listings/{listing_id}/images",
    response_model=UploadListingImagesResponse,
    status_code=status.HTTP_200_OK
)
def upload_listing_images(
    listing_id: UUID,
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db)
):

    return upload_listing_images_service(
        db=db,
        listing_id=listing_id,
        files=files
    )

# DELETE LISTING IMAGE
@router.delete(
    "/listings/{listing_id}/images/{image_id}",
    response_model=DeleteListingImageResponse,
    status_code=status.HTTP_200_OK
)
def delete_listing_image(
    listing_id: UUID,
    image_id: str,
    db: Session = Depends(get_db)
):
    return delete_listing_image_service(
        db=db,
        listing_id=listing_id,
        image_id=image_id
    )

# CREATE CUSTOM ATTRIBUTE
@router.post(
    "/attributes",
    response_model=MerchantCustomAttributeResponse,
    status_code=status.HTTP_201_CREATED
)
def create_custom_attribute(
    payload: MerchantCustomAttributeCreate,
    db: Session = Depends(get_db)
):

    return create_custom_attribute_service(
        db,
        payload
    )

# GET MERCHANT ATTRIBUTES
@router.get(
    "/attributes",
    response_model=List[MerchantAttributeListResponse],
    status_code=status.HTTP_200_OK
)
def get_merchant_attributes(
    db: Session = Depends(get_db)
):

    return get_merchant_attributes_service(db)

# MAP ATTRIBUTE TO BUSINESS
@router.post(
    "/business/{bussiness_id}/attributes",
    response_model=BusinessAttributeMapResponse,
    status_code=status.HTTP_201_CREATED
)
def map_attribute_to_business(
    business_id: UUID,
    payload: BusinessAttributeMapCreate,
    db: Session = Depends(get_db)
):

    return map_attribute_to_business_service(
        db,
        business_id,
        payload
    )

# MAP ATTRIBUTE TO LISTING
@router.post(
    "/listings/{listing_id}/attributes",
    response_model=ListingAttributeMapResponse,
    status_code=status.HTTP_201_CREATED
)
def map_attribute_to_listing(
    listing_id: UUID,
    payload: ListingAttributeMapCreate,
    db: Session = Depends(get_db)
):
    return map_attribute_to_listing_service(
        db,
        listing_id,
        payload
    )

@router.get(
    "/bookings",
    response_model=MerchantBookingList,
    status_code=status.HTTP_200_OK
)
def get_merchant_bookings(
    status_filter: str = Query(
        default=None,
        alias="status"
    ),
    booking_date: date = Query(
        default=None
    ),
    page: int = Query(
        ...,
        gt=0
    ),
    size: int = Query(
        ...,
        gt=0
    ),
    db: Session = Depends(get_db)
):
    return get_merchant_bookings_service(
        db=db,
        page=page,
        size=size,
        booking_status=status_filter,
        booking_date=booking_date
    )

@router.put(
    "/bookings/{booking_id}/status",
    response_model=BookingStatusUpdateResponse,
    status_code=status.HTTP_200_OK
)
def update_booking_status(
    booking_id: UUID,
    payload: BookingStatusUpdate,
    db: Session = Depends(get_db)
):

    return update_booking_status_service(
        db=db,
        booking_id=booking_id,
        payload=payload
    )
