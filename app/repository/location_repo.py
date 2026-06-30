from uuid import UUID

from sqlalchemy.orm import Session

from app.models.location_model import EnterpriseLocation
from app.repository.query_utils import apply_soft_delete_filter, paginate_query


def create_location(db: Session, enterprise_id: UUID, location_data):
    location = EnterpriseLocation(
        enterprise_id=enterprise_id,
        **location_data.model_dump(),
    )
    db.add(location)
    db.commit()
    db.refresh(location)
    return location


def get_locations_by_enterprise(
    db: Session,
    enterprise_id: UUID,
    *,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    include_deleted: bool = False,
):
    query = db.query(EnterpriseLocation).filter(
        EnterpriseLocation.enterprise_id == enterprise_id,
    )
    query = apply_soft_delete_filter(query, EnterpriseLocation, include_deleted)

    if status:
        query = query.filter(EnterpriseLocation.status == status)

    query = query.order_by(EnterpriseLocation.created_at.desc())
    return paginate_query(query, page, page_size)


def get_location_by_id(db: Session, location_id: UUID, include_deleted: bool = False):
    query = db.query(EnterpriseLocation).filter(EnterpriseLocation.id == location_id)
    if not include_deleted:
        query = apply_soft_delete_filter(query, EnterpriseLocation, include_deleted)
    return query.first()


def update_location(db: Session, location, update_data):
    for key, value in update_data.model_dump(exclude_unset=True).items():
        setattr(location, key, value)
    db.commit()
    db.refresh(location)
    return location


def delete_location(db: Session, location):
    location.is_deleted = True
    location.status = "inactive"
    db.commit()
    db.refresh(location)
    return location
