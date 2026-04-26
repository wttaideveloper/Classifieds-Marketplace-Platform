from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.services.merchant_service import get_merchant_profile_service, update_merchant_profile_service
from app.core.dependencies import get_current_user
from app.schemas.merchant_schema import MerchantProfileUpdate

router = APIRouter(
    prefix="/merchant",
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