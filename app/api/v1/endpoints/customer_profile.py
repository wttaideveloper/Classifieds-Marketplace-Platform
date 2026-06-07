from fastapi import APIRouter, Depends, status, Query
from sqlalchemy.orm import Session
from app.core.dependencies import get_db
from app.schemas.customer_schema import CustomerProfileResponse, CustomerProfileUpdate, CustomerBookingList
from app.services.customer_service import (
    get_profile_service,
    update_profile_service,
    get_customer_bookings_service
)
from uuid import UUID

router = APIRouter(
    tags=["Customer Profile"]
)

#  GET PROFILE
@router.get("/profile", response_model=CustomerProfileResponse, status_code=status.HTTP_200_OK)
def get_profile(
    customer_id: UUID,
    db: Session = Depends(get_db)
):
    return get_profile_service(db, customer_id)

# UPDATE PROFILE
@router.put("/{customer_id}/profile", response_model=CustomerProfileResponse, status_code=status.HTTP_200_OK)
def update_profile(
    customer_id: UUID,
    payload: CustomerProfileUpdate,
    db: Session = Depends(get_db)
):
    return update_profile_service(
        db,
        customer_id,
        payload.model_dump(exclude_unset=True)
    )

@router.get(
    "/bookings",
    response_model=CustomerBookingList,
    status_code=status.HTTP_200_OK
)
def get_customer_bookings(
    status_filter: str = Query(
        default=None,
        alias="status"
    ),
    page: int = Query(..., gt=0),
    size: int = Query(..., gt=0),
    db: Session = Depends(get_db)
):

    return get_customer_bookings_service(
        db=db,
        page=page,
        size=size,
        booking_status=status_filter
    )