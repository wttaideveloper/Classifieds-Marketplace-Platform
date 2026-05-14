from fastapi import APIRouter, Depends, status, UploadFile, File, Query
from typing import List, Optional
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
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
    delete_listing_image_service
)
from app.schemas.merchant_schema import (
    MerchantProfileUpdate, 
    MerchantBusinessProfileCreate, 
    MerchantBusinessDraft, 
    UpdateBusinessProfile,
    MerchantListingCreate,
    MerchantDraftCreate,
    UpdateMerchantListing
)

router = APIRouter(
    tags=["Merchant"]
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/profile", status_code=status.HTTP_200_OK)
def get_profile(
    merchant_id: str = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return get_merchant_profile_service(db, merchant_id)

# UPDATE PROFILE API
@router.put("/profile", status_code=status.HTTP_200_OK)
def update_profile(
    payload: MerchantProfileUpdate,
    merchant_id: str = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return update_merchant_profile_service(
        db,
        merchant_id,
        payload.dict()
    )

@router.post("/business", status_code=status.HTTP_201_CREATED)
def create_business_profile(
    payload: MerchantBusinessProfileCreate,
    merchant_id: str = Query(..., description="Merchant id"),
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
    merchant_id: str = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return save_business_draft_service(
        db,
        merchant_id,
        payload
    )

@router.get(
    "/business",
    status_code=status.HTTP_200_OK
)
def get_my_business_profile(
    merchant_id: str = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return get_business_profile_service(
        db,
        merchant_id
    )

@router.put(
    "/business",
    status_code=status.HTTP_200_OK
)
def update_business_profile(
    payload: UpdateBusinessProfile,
    merchant_id: str = Query(..., description="Merchant id"),
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
    merchant_id: str = Query(..., description="Merchant id"),
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
    merchant_id: str = Query(..., description="Merchant id"),
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
    merchant_id: str = Query(..., description="Merchant id"),
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
    merchant_id: str = Query(..., description="Merchant id"),
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
    merchant_id: str = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return delete_business_gallery_image_service(
        db,
        merchant_id,
        image_id
    )

@router.get(
    "/business/status",
    status_code=status.HTTP_200_OK
)
def get_business_status(
    merchant_id: str = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return get_business_status_service(
        db,
        merchant_id
    )

# CREATE LISTING
@router.post(
    "/listings",
    status_code=status.HTTP_201_CREATED
)
def create_listing(
    payload: MerchantListingCreate,
    merchant_id: str = Query(..., description="Merchant id"),
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
    status_code=status.HTTP_201_CREATED
)
def save_listing_as_draft(
    payload: MerchantDraftCreate,
    merchant_id: str = Query(..., description="Merchant id"),
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
    status_code=status.HTTP_200_OK
)
def get_my_listings(
    merchant_id: str = Query(..., description="Merchant id"),
    businessId: Optional[str] = None,
    status_filter: str = Query(
        default=None,
        alias="status"
    ),
    listingType: str = None,
    search: str = None,
    page: int = 1,
    limit: int = 10,
    db: Session = Depends(get_db)
):
    return get_my_listings_service(
        db=db,
        merchant_id=merchant_id,
        businessId=businessId,
        status_filter=status_filter,
        listingType=listingType,
        search=search,
        page=page,
        limit=limit
    )

# GET LISTING DETAILS
@router.get(
    "/listings/{listingId}",
    status_code=status.HTTP_200_OK
)
def get_listing_details(
    listingId: str,
    db: Session = Depends(get_db)
):
    return get_listing_details_service(
        db=db,
        listingId=listingId
    )

# UPDATE LISTING
@router.put(
    "/listings/{listingId}",
    status_code=status.HTTP_200_OK
)
def update_listing(
    listingId: str,
    payload: UpdateMerchantListing,
    merchant_id: str = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return update_listing_service(
        db=db,
        merchant_id=merchant_id,
        listingId=listingId,
        payload=payload
    )

# DELETE LISTING
@router.delete(
    "/listings/{listingId}",
    status_code=status.HTTP_200_OK
)
def delete_listing(
    listingId: str,
    merchantId: str = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return delete_listing_service(
        db=db,
        merchant_id=merchantId,
        listingId=listingId
    )

# PUBLISH LISTING
@router.patch(
    "/listings/{listingId}/publish",
    status_code=status.HTTP_200_OK
)
def publish_listing(
    listingId: str,
    merchant_id: str = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return publish_listing_service(
        db=db,
        merchant_id=merchant_id,
        listingId=listingId
    )

# UNPUBLISH LISTING
@router.patch(
    "/listings/{listingId}/unpublish",
    status_code=status.HTTP_200_OK
)
def unpublish_listing(
    listingId: str,
    merchantId: str = Query(..., description="Merchant id"),
    db: Session = Depends(get_db)
):
    return unpublish_listing_service(
        db=db,
        merchant_id=merchantId,
        listingId=listingId
    )

# UPLOAD LISTING IMAGES
@router.post(
    "/listings/{listingId}/images",
    status_code=status.HTTP_200_OK
)
def upload_listing_images(
    listingId: str,
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db)
):

    return upload_listing_images_service(
        db=db,
        listingId=listingId,
        files=files
    )

# DELETE LISTING IMAGE
@router.delete(
    "/listings/{listingId}/images/{imageId}",
    status_code=status.HTTP_200_OK
)
def delete_listing_image(
    listingId: str,
    imageId: str,
    db: Session = Depends(get_db)
):
    return delete_listing_image_service(
        db=db,
        listingId=listingId,
        imageId=imageId
    )