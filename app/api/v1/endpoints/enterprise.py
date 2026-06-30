from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.enterprise_schema import (
    EnterpriseCreate,
    EnterpriseDetailResponse,
    EnterprisePaginatedResponse,
    EnterpriseResponse,
    EnterpriseUpdate,
)
from app.services.enterprise_service import (
    create_enterprise_service,
    delete_enterprise_service,
    get_all_enterprises_service,
    get_enterprise_service,
    update_enterprise_service,
)

router = APIRouter(tags=["Enterprise"])


@router.post(
    "/",
    response_model=EnterpriseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Enterprise",
)
def create_enterprise(
    enterprise: EnterpriseCreate = Body(...),
    db: Session = Depends(get_db),
):
    return create_enterprise_service(db, enterprise)


@router.get(
    "/",
    response_model=EnterprisePaginatedResponse,
    status_code=status.HTTP_200_OK,
    summary="Get All Enterprises",
)
def get_enterprises(
    search: str | None = Query(None, description="Search across enterprise fields."),
    status_filter: str | None = Query(None, alias="status", description="Filter by status."),
    tenant_id: UUID | None = Query(None, description="Filter by tenant ID."),
    page: int = Query(DEFAULT_PAGE, ge=1, description="Page number."),
    page_size: int = Query(
        DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description="Items per page.",
    ),
    db: Session = Depends(get_db),
):
    return get_all_enterprises_service(
        db,
        search=search,
        status_filter=status_filter,
        tenant_id=tenant_id,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{enterprise_id}",
    response_model=EnterpriseDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Enterprise By ID",
)
def get_enterprise(
    enterprise_id: UUID = Path(..., description="Unique identifier of the enterprise"),
    db: Session = Depends(get_db),
):
    return get_enterprise_service(db, enterprise_id)


@router.put(
    "/{enterprise_id}",
    response_model=EnterpriseResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Enterprise",
)
def update_enterprise(
    enterprise: EnterpriseUpdate,
    enterprise_id: UUID = Path(..., description="Unique identifier of the enterprise"),
    db: Session = Depends(get_db),
):
    return update_enterprise_service(db, enterprise_id, enterprise)


@router.delete(
    "/{enterprise_id}",
    status_code=status.HTTP_200_OK,
    summary="Deactivate Enterprise",
)
def delete_enterprise(
    enterprise_id: UUID = Path(..., description="Unique identifier of the enterprise"),
    db: Session = Depends(get_db),
):
    delete_enterprise_service(db, enterprise_id)
    return {"message": "Enterprise marked inactive successfully"}
