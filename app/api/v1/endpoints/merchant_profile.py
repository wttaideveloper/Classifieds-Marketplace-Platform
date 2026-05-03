from fastapi import APIRouter, Depends, status, UploadFile, File
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
    get_business_status_service
)
from app.core.dependencies import get_current_user
from app.schemas.merchant_schema import MerchantProfileUpdate, MerchantBusinessProfileCreate, MerchantBusinessDraft, UpdateBusinessProfile

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