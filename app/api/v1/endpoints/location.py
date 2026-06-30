from uuid import UUID

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.location_schema import LocationResponse, LocationUpdate
from app.services.location_service import (
    delete_location_service,
    get_location_service,
    update_location_service,
)

router = APIRouter(tags=["Enterprise Locations"])


@router.get(
    "/{location_id}",
    response_model=LocationResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Location By ID",
)
def get_location(
    location_id: UUID = Path(..., description="Location identifier"),
    db: Session = Depends(get_db),
):
    return get_location_service(db, location_id)


@router.put(
    "/{location_id}",
    response_model=LocationResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Location",
)
def update_location(
    location: LocationUpdate,
    location_id: UUID = Path(..., description="Location identifier"),
    db: Session = Depends(get_db),
):
    return update_location_service(db, location_id, location)


@router.delete(
    "/{location_id}",
    status_code=status.HTTP_200_OK,
    summary="Deactivate Location",
)
def delete_location(
    location_id: UUID = Path(..., description="Location identifier"),
    db: Session = Depends(get_db),
):
    delete_location_service(db, location_id)
    return {"message": "Location marked inactive successfully"}
