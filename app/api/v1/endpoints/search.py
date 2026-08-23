from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.enterprise_schema import EnterprisePaginatedResponse
from app.schemas.event_schema import EventPaginatedResponse
from app.schemas.product_schema import ProductPaginatedResponse
from app.schemas.program_schema import ProgramPaginatedResponse
from app.schemas.service_schema import ServicePaginatedResponse
from app.schemas.training_schema import TrainingPaginatedResponse
from app.services.search_service import (
    search_enterprises_service,
    search_events_service,
    search_products_service,
    search_programs_service,
    search_services_service,
    search_trainings_service,
)

router = APIRouter(tags=["Search"])


@router.get(
    "/enterprises",
    response_model=EnterprisePaginatedResponse,
    summary="Search Enterprises",
)
def search_enterprises(
    query: str | None = Query(None, description="Search query."),
    tenant_id: UUID | None = Query(None, description="Filter by tenant ID."),
    enterprise_id: UUID | None = Query(None, description="Filter by enterprise ID."),
    category: str | None = Query(None, description="Filter by category."),
    city: str | None = Query(None, description="Filter by city."),
    status_filter: str | None = Query(None, alias="status", description="Filter by status."),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
):
    return search_enterprises_service(
        db,
        query=query,
        tenant_id=tenant_id,
        enterprise_id=enterprise_id,
        category=category,
        city=city,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/products",
    response_model=ProductPaginatedResponse,
    summary="Search Products",
)
def search_products(
    query: str | None = Query(None, description="Search query."),
    tenant_id: UUID | None = Query(None, description="Filter by tenant ID."),
    enterprise_id: UUID | None = Query(None, description="Filter by enterprise ID."),
    category: str | None = Query(None, description="Filter by category."),
    city: str | None = Query(None, description="Filter by city."),
    status_filter: str | None = Query(None, alias="status", description="Filter by status."),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
):
    return search_products_service(
        db,
        query=query,
        tenant_id=tenant_id,
        enterprise_id=enterprise_id,
        category=category,
        city=city,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/services",
    response_model=ServicePaginatedResponse,
    summary="Search Services",
)
def search_services(
    query: str | None = Query(None, description="Search query."),
    tenant_id: UUID | None = Query(None, description="Filter by tenant ID."),
    enterprise_id: UUID | None = Query(None, description="Filter by enterprise ID."),
    category: str | None = Query(None, description="Filter by category."),
    city: str | None = Query(None, description="Filter by city."),
    status_filter: str | None = Query(None, alias="status", description="Filter by status."),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
):
    return search_services_service(
        db,
        query=query,
        tenant_id=tenant_id,
        enterprise_id=enterprise_id,
        category=category,
        city=city,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/events",
    response_model=EventPaginatedResponse,
    summary="Search Events",
)
def search_events(
    query: str | None = Query(None, description="Search query."),
    tenant_id: UUID | None = Query(None),
    enterprise_id: UUID | None = Query(None),
    category: str | None = Query(None),
    city: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    delivery_mode: str | None = Query(None),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
):
    return search_events_service(
        db, query=query, tenant_id=tenant_id, enterprise_id=enterprise_id, category=category, city=city, status_filter=status_filter, delivery_mode=delivery_mode, page=page, page_size=page_size,
    )


@router.get(
    "/trainings",
    response_model=TrainingPaginatedResponse,
    summary="Search Trainings",
)
def search_trainings(
    query: str | None = Query(None),
    tenant_id: UUID | None = Query(None),
    enterprise_id: UUID | None = Query(None),
    category: str | None = Query(None),
    city: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
):
    return search_trainings_service(
        db, query=query, tenant_id=tenant_id, enterprise_id=enterprise_id, category=category, city=city, status_filter=status_filter, page=page, page_size=page_size,
    )


@router.get(
    "/programs",
    response_model=ProgramPaginatedResponse,
    summary="Search Programs",
)
def search_programs(
    query: str | None = Query(None),
    tenant_id: UUID | None = Query(None),
    enterprise_id: UUID | None = Query(None),
    category: str | None = Query(None),
    city: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
):
    return search_programs_service(
        db, query=query, tenant_id=tenant_id, enterprise_id=enterprise_id, category=category, city=city, status_filter=status_filter, page=page, page_size=page_size,
    )
