from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.location_schema import (
    LocationCreate,
    LocationPaginatedResponse,
    LocationResponse,
    LocationUpdate,
)
from app.services.location_service import (
    create_location_service,
    delete_location_service,
    get_location_service,
    get_locations_by_enterprise_service,
    update_location_service,
)

router = APIRouter(tags=["Enterprise Locations"])


@router.post(
    "/{enterprise_id}/locations",
    response_model=LocationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Enterprise Location",
)
def create_enterprise_location(
    location: LocationCreate,
    enterprise_id: UUID = Path(..., description="Enterprise identifier"),
    db: Session = Depends(get_db),
):
    return create_location_service(db, enterprise_id, location)


@router.get(
    "/{enterprise_id}/locations",
    response_model=LocationPaginatedResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Enterprise Locations",
)
def get_enterprise_locations(
    enterprise_id: UUID = Path(..., description="Enterprise identifier"),
    status_filter: str | None = Query(None, alias="status", description="Filter by status."),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
):
    return get_locations_by_enterprise_service(
        db,
        enterprise_id,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )
