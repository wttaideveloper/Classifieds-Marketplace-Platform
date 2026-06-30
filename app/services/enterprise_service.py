from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.repository.enterprise_repo import (
    create_enterprise,
    delete_enterprise,
    get_enterprise_by_email,
    get_enterprise_by_id,
    get_enterprises,
    update_enterprise,
)
from app.repository.query_utils import build_pagination_meta
from app.schemas.enterprise_schema import (
    EnterpriseDetailResponse,
    EnterpriseListItemResponse,
    EnterprisePaginatedResponse,
    EnterpriseResponse,
)
from app.services.response_mappers import (
    map_enterprise_detail,
    map_enterprise_list_item,
    map_enterprise_write,
)


def create_enterprise_service(db: Session, enterprise_data):
    existing = get_enterprise_by_email(db, enterprise_data.business_email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Business email already exists",
        )

    return EnterpriseResponse.model_validate(
        map_enterprise_write(create_enterprise(db, enterprise_data))
    )


def get_all_enterprises_service(
    db: Session,
    *,
    search: str | None = None,
    status_filter: str | None = None,
    tenant_id: UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> EnterprisePaginatedResponse:
    items, total = get_enterprises(
        db,
        search=search,
        status=status_filter,
        tenant_id=tenant_id,
        page=page,
        page_size=page_size,
    )
    return EnterprisePaginatedResponse(
        items=[
            EnterpriseListItemResponse.model_validate(map_enterprise_list_item(enterprise))
            for enterprise in items
        ],
        pagination=build_pagination_meta(total, page, page_size),
    )


def get_enterprise_service(db: Session, enterprise_id: UUID) -> EnterpriseDetailResponse:
    enterprise = get_enterprise_by_id(db, enterprise_id)
    if not enterprise:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enterprise not found",
        )

    return EnterpriseDetailResponse.model_validate(map_enterprise_detail(enterprise))


def update_enterprise_service(db: Session, enterprise_id: UUID, update_data):
    enterprise = get_enterprise_by_id(db, enterprise_id, include_deleted=True)
    if not enterprise or enterprise.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enterprise not found",
        )

    return EnterpriseResponse.model_validate(
        map_enterprise_write(update_enterprise(db, enterprise, update_data))
    )


def delete_enterprise_service(db: Session, enterprise_id: UUID):
    enterprise = get_enterprise_by_id(db, enterprise_id)
    if not enterprise:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enterprise not found",
        )

    return delete_enterprise(db, enterprise)
