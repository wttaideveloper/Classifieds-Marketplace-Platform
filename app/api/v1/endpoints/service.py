from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.service_schema import (
    ServiceCreate,
    ServiceDetailResponse,
    ServicePaginatedResponse,
    ServiceResponse,
    ServiceUpdate,
)
from app.services.service_service import (
    create_service_service,
    delete_service_service,
    get_service_service,
    get_services_service,
    update_service_service,
)

router = APIRouter(tags=["Services"])


@router.post(
    "/",
    response_model=ServiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Service",
)
def create_service(
    service: ServiceCreate,
    db: Session = Depends(get_db),
):
    return create_service_service(db, service)


@router.get(
    "/",
    response_model=ServicePaginatedResponse,
    status_code=status.HTTP_200_OK,
    summary="Get All Services",
)
def get_services(
    search: str | None = Query(None, description="Search across service fields."),
    tenant_id: UUID | None = Query(None, description="Filter by tenant ID."),
    enterprise_id: UUID | None = Query(None, description="Filter by enterprise ID."),
    category: str | None = Query(None, description="Filter by category."),
    location_id: UUID | None = Query(None, description="Filter by location ID."),
    status_filter: str | None = Query(None, alias="status", description="Filter by status."),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
):
    return get_services_service(
        db,
        search=search,
        tenant_id=tenant_id,
        enterprise_id=enterprise_id,
        category=category,
        location_id=location_id,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{service_id}",
    response_model=ServiceDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Service By ID",
)
def get_service(
    service_id: UUID = Path(..., description="Unique identifier of the service"),
    db: Session = Depends(get_db),
):
    return get_service_service(db, service_id)


@router.put(
    "/{service_id}",
    response_model=ServiceResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Service",
)
def update_service(
    service: ServiceUpdate,
    service_id: UUID = Path(..., description="Unique identifier of the service"),
    db: Session = Depends(get_db),
):
    return update_service_service(db, service_id, service)


@router.delete(
    "/{service_id}",
    status_code=status.HTTP_200_OK,
    summary="Deactivate Service",
)
def delete_service(
    service_id: UUID = Path(..., description="Unique identifier of the service"),
    db: Session = Depends(get_db),
):
    delete_service_service(db, service_id)
    return {"message": "Service marked inactive successfully"}
