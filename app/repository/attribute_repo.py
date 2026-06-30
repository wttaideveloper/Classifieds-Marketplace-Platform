from uuid import UUID

from sqlalchemy.orm import Session

from app.models.attribute_model import DynamicAttribute
from app.repository.query_utils import apply_soft_delete_filter, paginate_query


def create_attribute(db: Session, attribute_data):
    attribute = DynamicAttribute(**attribute_data.model_dump())
    db.add(attribute)
    db.commit()
    db.refresh(attribute)
    return attribute


def get_attributes(
    db: Session,
    *,
    tenant_id: UUID | None = None,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    include_deleted: bool = False,
):
    query = db.query(DynamicAttribute)
    query = apply_soft_delete_filter(query, DynamicAttribute, include_deleted)

    if tenant_id:
        query = query.filter(DynamicAttribute.tenant_id == tenant_id)
    if entity_type:
        query = query.filter(DynamicAttribute.entity_type == entity_type.lower())
    if entity_id:
        query = query.filter(DynamicAttribute.entity_id == entity_id)
    if status:
        query = query.filter(DynamicAttribute.status == status)

    query = query.order_by(DynamicAttribute.created_at.desc())
    return paginate_query(query, page, page_size)


def get_attribute_by_id(db: Session, attribute_id: UUID, include_deleted: bool = False):
    query = db.query(DynamicAttribute).filter(DynamicAttribute.id == attribute_id)
    if not include_deleted:
        query = apply_soft_delete_filter(query, DynamicAttribute, include_deleted)
    return query.first()


def update_attribute(db: Session, attribute, update_data):
    for key, value in update_data.model_dump(exclude_unset=True).items():
        setattr(attribute, key, value)
    db.commit()
    db.refresh(attribute)
    return attribute


def delete_attribute(db: Session, attribute):
    attribute.is_deleted = True
    attribute.status = "inactive"
    db.commit()
    db.refresh(attribute)
    return attribute
