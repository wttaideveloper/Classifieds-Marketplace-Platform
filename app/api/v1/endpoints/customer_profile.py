from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from app.core.dependencies import get_db
from app.schemas.customer_schema import CustomerProfileUpdate
from app.services.customer_service import (
    get_profile_service,
    update_profile_service
)

router = APIRouter(
    tags=["Customer Profile"]
)

#  GET PROFILE
@router.get("/profile", status_code=status.HTTP_200_OK)
def get_profile(
    db: Session = Depends(get_db)
):
    return get_profile_service(db)

# UPDATE PROFILE
@router.put("/profile", status_code=status.HTTP_200_OK)
def update_profile(
    payload: CustomerProfileUpdate,
    db: Session = Depends(get_db)
):
    return update_profile_service(
        db,
        payload.model_dump(exclude_unset=True)
    )