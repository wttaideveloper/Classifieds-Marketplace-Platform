from uuid import UUID

from fastapi import HTTPException
from fastapi import status

from sqlalchemy.orm import Session

from app.models.enterprise_model import Enterprise
from app.models.product_model import Product
from app.models.service_model import Service

from app.repository.attribute_repo import (
    create_attribute,
    get_attributes,
    get_attribute_by_id,
    update_attribute,
    delete_attribute
)


def validate_entity(
    db: Session,
    entity_type: str,
    entity_id: UUID
):
    entity_type = entity_type.lower()

    if entity_type == "enterprise":
        entity = (
            db.query(Enterprise)
            .filter(
                Enterprise.id == entity_id
            )
            .first()
        )

    elif entity_type == "product":
        entity = (
            db.query(Product)
            .filter(
                Product.id == entity_id
            )
            .first()
        )

    elif entity_type == "service":
        entity = (
            db.query(Service)
            .filter(
                Service.id == entity_id
            )
            .first()
        )

    else:
        entity = None

    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{entity_type.capitalize()} not found"
        )

    return entity


def create_attribute_service(
    db: Session,
    attribute_data
):
    validate_entity(
        db,
        attribute_data.entity_type,
        attribute_data.entity_id
    )

    return create_attribute(
        db,
        attribute_data
    )


def get_attributes_service(
    db: Session,
    entity_type: str,
    entity_id: UUID
):
    return get_attributes(
        db,
        entity_type,
        entity_id
    )


def update_attribute_service(
    db: Session,
    attribute_id: UUID,
    update_data
):
    attribute = get_attribute_by_id(
        db,
        attribute_id
    )

    if not attribute:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attribute not found"
        )

    return update_attribute(
        db,
        attribute,
        update_data
    )


def delete_attribute_service(
    db: Session,
    attribute_id: UUID
):
    attribute = get_attribute_by_id(
        db,
        attribute_id
    )

    if not attribute:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attribute not found"
        )

    delete_attribute(
        db,
        attribute
    )