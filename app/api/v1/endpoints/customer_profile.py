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
@router.get("/{customer_id}/profile", status_code=status.HTTP_200_OK)
def get_profile(
    customer_id: str,
    db: Session = Depends(get_db)
):
    return get_profile_service(db, customer_id)

# UPDATE PROFILE
@router.put("/{customer_id}/profile", status_code=status.HTTP_200_OK)
def update_profile(
    customer_id: str,
    payload: CustomerProfileUpdate,
    db: Session = Depends(get_db)
):
    return update_profile_service(
        db,
        customer_id,
        payload.model_dump(exclude_unset=True)
    )