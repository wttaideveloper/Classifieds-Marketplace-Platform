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
    CapacityUpdate,
    CapacityStatusUpdate
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
    status_code=status.HTTP_201_CREATED
)
def create_capacity(
    payload: CapacityCreate,
    db: Session = Depends(get_db)
):
    return create_capacity_service(
        db,
        payload
    )

@router.get("/{listing_id}")
def get_capacity(
    listing_id: UUID,
    db: Session = Depends(get_db)
):
    return get_capacity_service(
        db,
        listing_id
    )

@router.put("/{listing_id}")
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

@router.get("/{listing_id}/availability")
def get_capacity_availability(
    listing_id: UUID,
    db: Session = Depends(get_db)
):

    return get_capacity_availability_service(
        db,
        listing_id
    )

@router.put("/{listing_id}/status")
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