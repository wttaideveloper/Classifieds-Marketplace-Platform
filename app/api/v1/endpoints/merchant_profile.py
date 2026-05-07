from fastapi import APIRouter, Depends, status, UploadFile, File, Query
from typing import List
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
from app.core.dependencies import get_current_user
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
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    return get_merchant_profile_service(db, current_user["id"])

# UPDATE PROFILE API
@router.put("/profile", status_code=status.HTTP_200_OK)
def update_profile(
    payload: MerchantProfileUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    return update_merchant_profile_service(
        db,
        current_user["id"],
        payload.dict()
    )

@router.post("/business", status_code=status.HTTP_201_CREATED)
def create_business_profile(
    payload: MerchantBusinessProfileCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    return create_business_profile_service(
        db,
        current_user["id"],
        payload
    )

@router.post(
    "/business/draft",
    status_code=status.HTTP_200_OK
)
def save_business_draft(
    payload: MerchantBusinessDraft,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    return save_business_draft_service(
        db,
        current_user["id"],
        payload
    )

@router.get(
    "/business",
    status_code=status.HTTP_200_OK
)
def get_my_business_profile(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    return get_business_profile_service(
        db,
        current_user["id"]
    )

@router.put(
    "/business",
    status_code=status.HTTP_200_OK
)
def update_business_profile(
    payload: UpdateBusinessProfile,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    return update_business_profile_service(
        db,
        current_user["id"],
        payload
    )

@router.post(
    "/business/submit",
    status_code=status.HTTP_200_OK
)
def submit_business_for_approval(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    return submit_business_for_approval_service(
        db,
        current_user["id"]
    )

@router.post(
    "/business/logo",
    status_code=status.HTTP_200_OK
)
def upload_business_logo(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    return upload_business_logo_service(
        db,
        current_user["id"],
        file
    )

@router.post(
    "/business/banner",
    status_code=status.HTTP_200_OK
)
def upload_business_banner(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    return upload_business_banner_service(
        db,
        current_user["id"],
        file
    )

@router.post(
    "/business/gallery",
    status_code=status.HTTP_200_OK
)
def upload_business_gallery(
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    return upload_business_gallery_service(
        db,
        current_user["id"],
        files
    )

@router.delete(
    "/business/gallery/{image_id}",
    status_code=status.HTTP_200_OK
)
def delete_business_gallery_image(
    image_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    return delete_business_gallery_image_service(
        db,
        current_user["id"],
        image_id
    )

@router.get(
    "/business/status",
    status_code=status.HTTP_200_OK
)
def get_business_status(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    return get_business_status_service(
        db,
        current_user["id"]
    )

# CREATE LISTING
@router.post(
    "/merchant/listings",
    status_code=status.HTTP_201_CREATED
)
def create_listing(
    payload: MerchantListingCreate,
    db: Session = Depends(get_db)
):
    return create_listing_service(db, payload)

# SAVE LISTING AS DRAFT
@router.post(
    "/merchant/listings/draft",
    status_code=status.HTTP_201_CREATED
)
def save_listing_as_draft(
    payload: MerchantDraftCreate,
    db: Session = Depends(get_db)
):
    return save_listing_draft_service(
        db=db,
        payload=payload
    )

# GET MY LISTINGS
@router.get(
    "/merchant/listings",
    status_code=status.HTTP_200_OK
)
def get_my_listings(
    businessId: str,
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
        businessId=businessId,
        status_filter=status_filter,
        listingType=listingType,
        search=search,
        page=page,
        limit=limit
    )

# GET LISTING DETAILS
@router.get(
    "/merchant/listings/{listingId}",
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
    "/merchant/listings/{listingId}",
    status_code=status.HTTP_200_OK
)
def update_listing(
    listingId: str,
    payload: UpdateMerchantListing,
    db: Session = Depends(get_db)
):
    return update_listing_service(
        db=db,
        listingId=listingId,
        payload=payload
    )

# DELETE LISTING
@router.delete(
    "/merchant/listings/{listingId}",
    status_code=status.HTTP_200_OK
)
def delete_listing(
    listingId: str,
    db: Session = Depends(get_db)
):
    return delete_listing_service(
        db=db,
        listingId=listingId
    )

# PUBLISH LISTING
@router.patch(
    "/merchant/listings/{listingId}/publish",
    status_code=status.HTTP_200_OK
)
def publish_listing(
    listingId: str,
    db: Session = Depends(get_db)
):
    return publish_listing_service(
        db=db,
        listingId=listingId
    )

# UNPUBLISH LISTING
@router.patch(
    "/merchant/listings/{listingId}/unpublish",
    status_code=status.HTTP_200_OK
)
def unpublish_listing(
    listingId: str,
    db: Session = Depends(get_db)
):
    return unpublish_listing_service(
        db=db,
        listingId=listingId
    )

# UPLOAD LISTING IMAGES
@router.post(
    "/merchant/listings/{listingId}/images",
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
    "/merchant/listings/{listingId}/images/{imageId}",
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