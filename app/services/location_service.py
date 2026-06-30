from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.enterprise_model import Enterprise
from app.models.location_model import EnterpriseLocation
from app.repository.location_repo import (
    create_location,
    delete_location,
    get_location_by_id,
    get_locations_by_enterprise,
    update_location,
)
from app.repository.query_utils import build_pagination_meta
from app.schemas.location_schema import LocationPaginatedResponse, LocationResponse
from app.services.response_mappers import map_location


def _get_enterprise_or_404(db: Session, enterprise_id: UUID) -> Enterprise:
    enterprise = (
        db.query(Enterprise)
        .filter(
            Enterprise.id == enterprise_id,
            Enterprise.is_deleted.is_(False),
        )
        .first()
    )
    if not enterprise:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enterprise not found",
        )
    return enterprise


def create_location_service(db: Session, enterprise_id: UUID, location_data):
    _get_enterprise_or_404(db, enterprise_id)
    location = create_location(db, enterprise_id, location_data)
    return LocationResponse.model_validate(map_location(location))


def get_locations_by_enterprise_service(
    db: Session,
    enterprise_id: UUID,
    *,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> LocationPaginatedResponse:
    _get_enterprise_or_404(db, enterprise_id)
    items, total = get_locations_by_enterprise(
        db,
        enterprise_id,
        status=status_filter,
        page=page,
        page_size=page_size,
    )
    return LocationPaginatedResponse(
        items=[LocationResponse.model_validate(map_location(item)) for item in items],
        pagination=build_pagination_meta(total, page, page_size),
    )


def get_location_service(db: Session, location_id: UUID) -> LocationResponse:
    location = get_location_by_id(db, location_id)
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Location not found",
        )
    return LocationResponse.model_validate(map_location(location))


def update_location_service(db: Session, location_id: UUID, update_data):
    location = get_location_by_id(db, location_id, include_deleted=True)
    if not location or location.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Location not found",
        )

    return LocationResponse.model_validate(
        map_location(update_location(db, location, update_data))
    )


def delete_location_service(db: Session, location_id: UUID):
    location = get_location_by_id(db, location_id)
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Location not found",
        )
    return delete_location(db, location)
