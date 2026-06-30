from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.models.service_model import Service
from app.repository.query_utils import (
    apply_ilike_search,
    apply_soft_delete_filter,
    paginate_query,
)


def create_service(db: Session, service_data):
    payload = service_data.to_model_data() if hasattr(service_data, "to_model_data") else service_data.model_dump()
    service = Service(**payload)
    db.add(service)
    db.commit()
    db.refresh(service)
    return service


def get_services(
    db: Session,
    *,
    search: str | None = None,
    tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None,
    category: str | None = None,
    location_id: UUID | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    include_deleted: bool = False,
):
    query = (
        db.query(Service)
        .options(joinedload(Service.enterprise))
    )
    query = apply_soft_delete_filter(query, Service, include_deleted)

    if tenant_id:
        query = query.filter(Service.tenant_id == tenant_id)
    if enterprise_id:
        query = query.filter(Service.enterprise_id == enterprise_id)
    if location_id:
        query = query.filter(Service.location_id == location_id)
    if category:
        query = query.filter(Service.service_category == category)
    if status:
        query = query.filter(Service.status == status)
    if search:
        query = apply_ilike_search(
            query,
            [
                Service.service_name,
                Service.service_description,
                Service.service_category,
                Service.provider_name,
                Service.instructor_name,
            ],
            search,
        )

    query = query.order_by(Service.created_at.desc())
    return paginate_query(query, page, page_size)


def get_service_by_id(db: Session, service_id: UUID, include_deleted: bool = False):
    query = (
        db.query(Service)
        .options(joinedload(Service.enterprise))
        .filter(Service.id == service_id)
    )
    if not include_deleted:
        query = apply_soft_delete_filter(query, Service, include_deleted)
    return query.first()


def update_service(db: Session, service, update_data):
    payload = update_data.to_model_data() if hasattr(update_data, "to_model_data") else update_data.model_dump(exclude_unset=True)
    for key, value in payload.items():
        setattr(service, key, value)
    db.commit()
    db.refresh(service)
    return service


def delete_service(db: Session, service):
    service.is_deleted = True
    service.status = "inactive"
    service.service_status = False
    db.commit()
    db.refresh(service)
    return service
