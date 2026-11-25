from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.catalog_access import validate_catalog_write
from app.models.enterprise_model import Enterprise
from app.models.product_model import Product
from app.models.service_model import Service
from app.repository.attribute_repo import (
    create_attribute,
    delete_attribute,
    get_attribute_by_id,
    get_attributes,
    update_attribute,
)
from app.repository.query_utils import build_pagination_meta
from app.schemas.attribute_schema import AttributePaginatedResponse, AttributeResponse


def validate_entity(db: Session, entity_type: str, entity_id: UUID):
    entity_type = entity_type.lower()

    if entity_type == "enterprise":
        entity = (
            db.query(Enterprise)
            .filter(
                Enterprise.id == entity_id,
                Enterprise.is_deleted.is_(False),
            )
            .first()
        )
    elif entity_type == "product":
        entity = (
            db.query(Product)
            .filter(
                Product.id == entity_id,
                Product.is_deleted.is_(False),
            )
            .first()
        )
    elif entity_type == "service":
        entity = (
            db.query(Service)
            .filter(
                Service.id == entity_id,
                Service.is_deleted.is_(False),
            )
            .first()
        )
    else:
        entity = None

    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{entity_type.capitalize()} not found",
        )

    return entity


def _owning_enterprise_id(entity_type: str, entity) -> UUID:
    return entity.id if entity_type.lower() == "enterprise" else entity.enterprise_id


def _check_writer(db: Session, entity_type: str, entity_id: UUID, access, supplied_tenant_id=None):
    """Only the Enterprise Admin of the business that owns the enterprise / product / service, or a Super Admin. 401 signed out, 403 anyone else.
    access=None is an internal caller and is not checked."""
    entity = validate_entity(db, entity_type, entity_id)
    validate_catalog_write(db, _owning_enterprise_id(entity_type, entity), supplied_tenant_id, access)
    return entity


def create_attribute_service(db: Session, attribute_data, access=None):
    _check_writer(db, attribute_data.entity_type, attribute_data.entity_id, access, attribute_data.tenant_id)

    attribute = create_attribute(db, attribute_data)
    return AttributeResponse.model_validate(attribute)


def get_attributes_service(
    db: Session,
    *,
    tenant_id: UUID | None = None,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> AttributePaginatedResponse:
    if entity_type and entity_id:
        validate_entity(db, entity_type, entity_id)

    items, total = get_attributes(
        db,
        tenant_id=tenant_id,
        entity_type=entity_type,
        entity_id=entity_id,
        status=status_filter,
        page=page,
        page_size=page_size,
    )
    return AttributePaginatedResponse(
        items=[AttributeResponse.model_validate(item) for item in items],
        pagination=build_pagination_meta(total, page, page_size),
    )


def get_attribute_service(db: Session, attribute_id: UUID) -> AttributeResponse:
    attribute = get_attribute_by_id(db, attribute_id)
    if not attribute:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attribute not found",
        )
    return AttributeResponse.model_validate(attribute)


def update_attribute_service(db: Session, attribute_id: UUID, update_data, access=None):
    attribute = get_attribute_by_id(db, attribute_id, include_deleted=True)
    if not attribute or attribute.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attribute not found",
        )
    _check_writer(db, attribute.entity_type, attribute.entity_id, access)

    attribute = update_attribute(db, attribute, update_data)
    return AttributeResponse.model_validate(attribute)


def delete_attribute_service(db: Session, attribute_id: UUID, access=None):
    attribute = get_attribute_by_id(db, attribute_id)
    if not attribute:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attribute not found",
        )
    _check_writer(db, attribute.entity_type, attribute.entity_id, access)

    delete_attribute(db, attribute)
