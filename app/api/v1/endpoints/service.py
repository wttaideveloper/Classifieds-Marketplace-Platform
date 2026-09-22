import logging
from uuid import UUID
from app.core.catalog_access import get_catalog_access, get_optional_catalog_user, require_catalog_writer

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
logger = logging.getLogger(__name__)


@router.post(
    "/",
    response_model=ServiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Service",
)
def create_service(
    service: ServiceCreate,
    db: Session = Depends(get_db),
    access=Depends(require_catalog_writer),
):
    return create_service_service(db, service, access=access)


@router.get(
    "/",
    response_model=ServicePaginatedResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Services (assigned listings for Providers)",
    description="Authenticated internal users/Providers are automatically restricted to their assigned listings in their tenant. No provider_user_id query parameter is needed.",
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
    access=Depends(get_catalog_access),
    user=Depends(get_optional_catalog_user),
):
    response = get_services_service(
        db,
        search=search,
        tenant_id=tenant_id,
        enterprise_id=enterprise_id,
        category=category,
        location_id=location_id,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
        access=access,
    )
    # TEMPORARY DIAGNOSTIC — remove once the GET /services empty-result
    # investigation is closed. No tokens/headers/PII: resolved identity and
    # access fields, requested pagination, and the final returned count only.
    # Never allowed to break the real response (wrapped defensively since
    # `response` may be a Pydantic model or, in mocked tests, a plain dict).
    try:
        pagination = getattr(response, "pagination", None)
        if pagination is None and isinstance(response, dict):
            pagination = response.get("pagination") or {}
        total = getattr(pagination, "total", None)
        if total is None and isinstance(pagination, dict):
            total = pagination.get("total")
        logger.info(
            "[DIAG GET /services] auth_user_id=%s auth_role=%s auth_tenant_role=%s | "
            "access.role=%s access.tenant_id=%s access.provider_user_id=%s | "
            "page=%s page_size=%s returned_count=%s",
            (user or {}).get("id"), (user or {}).get("role"), (user or {}).get("tenant_role"),
            access.role, access.tenant_id, access.provider_user_id,
            page, page_size, total,
        )
    except Exception:
        logger.debug("[DIAG GET /services] diagnostic logging failed", exc_info=True)
    return response


@router.get(
    "/{service_id}",
    response_model=ServiceDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Service By ID",
)
def get_service(
    service_id: UUID = Path(..., description="Unique identifier of the service"),
    db: Session = Depends(get_db),
    access=Depends(get_catalog_access),
):
    return get_service_service(db, service_id, access=access)


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
    access=Depends(require_catalog_writer),
):
    return update_service_service(db, service_id, service, access=access)


@router.delete(
    "/{service_id}",
    status_code=status.HTTP_200_OK,
    summary="Deactivate Service",
)
def delete_service(
    service_id: UUID = Path(..., description="Unique identifier of the service"),
    db: Session = Depends(get_db),
    access=Depends(require_catalog_writer),
):
    delete_service_service(db, service_id, access=access)
    return {"message": "Service marked inactive successfully"}
