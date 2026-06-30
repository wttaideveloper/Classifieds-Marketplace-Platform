from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.enterprise_model import Enterprise
from app.models.location_model import EnterpriseLocation
from app.repository.query_utils import build_pagination_meta
from app.repository.service_repo import (
    create_service,
    delete_service,
    get_service_by_id,
    get_services,
    update_service,
)
from app.schemas.service_schema import (
    ServiceDetailResponse,
    ServiceListItemResponse,
    ServicePaginatedResponse,
    ServiceResponse,
)
from app.services.response_mappers import (
    map_service_detail,
    map_service_list_item,
    map_service_write,
)


def _validate_references(db: Session, enterprise_id: UUID, location_id: UUID | None):
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

    if location_id:
        location = (
            db.query(EnterpriseLocation)
            .filter(
                EnterpriseLocation.id == location_id,
                EnterpriseLocation.enterprise_id == enterprise_id,
                EnterpriseLocation.is_deleted.is_(False),
            )
            .first()
        )
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Location not found for this enterprise",
            )


def create_service_service(db: Session, service_data):
    _validate_references(db, service_data.enterprise_id, service_data.location_id)
    return ServiceResponse.model_validate(
        map_service_write(create_service(db, service_data))
    )


def get_services_service(
    db: Session,
    *,
    search: str | None = None,
    tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None,
    category: str | None = None,
    location_id: UUID | None = None,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> ServicePaginatedResponse:
    items, total = get_services(
        db,
        search=search,
        tenant_id=tenant_id,
        enterprise_id=enterprise_id,
        category=category,
        location_id=location_id,
        status=status_filter,
        page=page,
        page_size=page_size,
    )
    return ServicePaginatedResponse(
        items=[
            ServiceListItemResponse.model_validate(map_service_list_item(service))
            for service in items
        ],
        pagination=build_pagination_meta(total, page, page_size),
    )


def get_service_service(db: Session, service_id: UUID) -> ServiceDetailResponse:
    service = get_service_by_id(db, service_id)
    if not service:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found",
        )

    return ServiceDetailResponse.model_validate(map_service_detail(service))


def update_service_service(db: Session, service_id: UUID, update_data):
    service = get_service_by_id(db, service_id, include_deleted=True)
    if not service or service.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found",
        )

    location_id = update_data.location_id if update_data.location_id is not None else service.location_id
    _validate_references(db, service.enterprise_id, location_id)

    return ServiceResponse.model_validate(
        map_service_write(update_service(db, service, update_data))
    )


def delete_service_service(db: Session, service_id: UUID):
    service = get_service_by_id(db, service_id)
    if not service:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found",
        )

    return delete_service(db, service)
