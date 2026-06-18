from uuid import UUID

from sqlalchemy.orm import Session

from app.models.attribute_model import DynamicAttribute


def create_attribute(
    db: Session,
    attribute_data
):
    attribute = DynamicAttribute(
        **attribute_data.dict()
    )

    db.add(attribute)
    db.commit()
    db.refresh(attribute)

    return attribute


def get_attributes(
    db: Session,
    entity_type: str,
    entity_id: UUID
):
    return (
        db.query(DynamicAttribute)
        .filter(
            DynamicAttribute.entity_type == entity_type,
            DynamicAttribute.entity_id == entity_id
        )
        .all()
    )


def get_attribute_by_id(
    db: Session,
    attribute_id: UUID
):
    return (
        db.query(DynamicAttribute)
        .filter(
            DynamicAttribute.id == attribute_id
        )
        .first()
    )


def update_attribute(
    db: Session,
    attribute,
    update_data
):
    for key, value in (
        update_data.dict(
            exclude_unset=True
        ).items()
    ):
        setattr(attribute, key, value)

    db.commit()
    db.refresh(attribute)

    return attribute


def delete_attribute(
    db: Session,
    attribute
):
    db.delete(attribute)
    db.commit()