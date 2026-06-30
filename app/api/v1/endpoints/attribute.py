from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.attribute_schema import (
    AttributeCreate,
    AttributePaginatedResponse,
    AttributeResponse,
    AttributeUpdate,
)
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.services.attribute_service import (
    create_attribute_service,
    delete_attribute_service,
    get_attribute_service,
    get_attributes_service,
    update_attribute_service,
)

router = APIRouter(tags=["Dynamic Attributes"])


@router.post(
    "",
    response_model=AttributeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Dynamic Attribute",
)
def create_attribute(
    attribute: AttributeCreate,
    db: Session = Depends(get_db),
):
    return create_attribute_service(db, attribute)


@router.get(
    "",
    response_model=AttributePaginatedResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Dynamic Attributes",
)
def get_attributes(
    tenant_id: UUID | None = Query(None, description="Filter by tenant ID."),
    entity_type: str | None = Query(None, description="Filter by entity type."),
    entity_id: UUID | None = Query(None, description="Filter by entity ID."),
    status_filter: str | None = Query(None, alias="status", description="Filter by status."),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
):
    return get_attributes_service(
        db,
        tenant_id=tenant_id,
        entity_type=entity_type,
        entity_id=entity_id,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{attribute_id}",
    response_model=AttributeResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Dynamic Attribute By ID",
)
def get_attribute(
    attribute_id: UUID = Path(..., description="Unique identifier of the attribute"),
    db: Session = Depends(get_db),
):
    return get_attribute_service(db, attribute_id)


@router.put(
    "/{attribute_id}",
    response_model=AttributeResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Dynamic Attribute",
)
def update_attribute(
    attribute: AttributeUpdate,
    attribute_id: UUID = Path(..., description="Unique identifier of the attribute"),
    db: Session = Depends(get_db),
):
    return update_attribute_service(db, attribute_id, attribute)


@router.delete(
    "/{attribute_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete Dynamic Attribute",
)
def delete_attribute(
    attribute_id: UUID = Path(..., description="Unique identifier of the attribute"),
    db: Session = Depends(get_db),
):
    delete_attribute_service(db, attribute_id)
    return {"message": "Attribute marked inactive successfully"}
