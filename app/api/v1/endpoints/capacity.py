from fastapi import (
    APIRouter,
    Depends,
    status
)
from sqlalchemy.orm import Session
from uuid import UUID
from app.db.database import get_db
from app.schemas.capacity_schema import (
    CapacityCreate,
    CapacityCreateResponse,
    CapacityDetailResponse,
    CapacityUpdate,
    CapacityUpdateEnvelopeResponse,
    CapacityAvailabilityEnvelopeResponse,
    CapacityStatusUpdate,
    CapacityStatusUpdateResponse
)
from app.services.capacity_service import (
    create_capacity_service,
    get_capacity_service,
    update_capacity_service,
    get_capacity_availability_service,
    update_capacity_status_service
)

router = APIRouter(
    tags=["Capacity"]
)

@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=CapacityCreateResponse
)
def create_capacity(
    payload: CapacityCreate,
    db: Session = Depends(get_db)
):
    return create_capacity_service(
        db,
        payload
    )

@router.get(
    "/{listing_id}",
    response_model=CapacityDetailResponse
)
def get_capacity(
    listing_id: UUID,
    db: Session = Depends(get_db)
):
    return get_capacity_service(
        db,
        listing_id
    )

@router.put(
    "/{listing_id}",
    response_model=CapacityUpdateEnvelopeResponse
)
def update_capacity(
    listing_id: UUID,
    payload: CapacityUpdate,
    db: Session = Depends(get_db)
):
    return update_capacity_service(
        db,
        listing_id,
        payload
    )

@router.get(
    "/{listing_id}/availability",
    response_model=CapacityAvailabilityEnvelopeResponse
)
def get_capacity_availability(
    listing_id: UUID,
    db: Session = Depends(get_db)
):

    return get_capacity_availability_service(
        db,
        listing_id
    )

@router.put(
    "/{listing_id}/status",
    response_model=CapacityStatusUpdateResponse
)
def update_capacity_status(
    listing_id: UUID,
    payload: CapacityStatusUpdate,
    db: Session = Depends(get_db)
):
    return update_capacity_status_service(
        db,
        listing_id,
        payload
    )
