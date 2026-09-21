import logging
from uuid import UUID
from app.core.catalog_access import scope_catalog_query

from sqlalchemy.orm import Session, joinedload

from app.models.service_model import Service
from app.repository.query_utils import (
    apply_ilike_search,
    apply_soft_delete_filter,
    paginate_query,
)

logger = logging.getLogger(__name__)

# TEMPORARY DIAGNOSTIC — remove once the GET /services empty-result
# investigation for this one service is closed.
_DIAG_SERVICE_ID = UUID("71fc4238-2b94-4826-99ad-fa3e79933a77")


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
    access=None,
):
    query = (
        db.query(Service)
        .options(joinedload(Service.enterprise))
    )
    query = scope_catalog_query(query, Service, access)
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
    items, total = paginate_query(query, page, page_size)

    # TEMPORARY DIAGNOSTIC — remove once the GET /services empty-result
    # investigation is closed. No tokens/PII: resolved access triple and
    # per-condition booleans for one known service row, evaluated independently.
    if access is not None and access.role == "provider":
        target = (
            db.query(Service)
            .options(joinedload(Service.enterprise))
            .filter(Service.id == _DIAG_SERVICE_ID)
            .first()
        )
        if target is None:
            logger.info("[DIAG get_services] target_service_id=%s not found in this database", _DIAG_SERVICE_ID)
        else:
            ent_tenant_id = target.enterprise.tenant_id if target.enterprise else None
            cond1 = ent_tenant_id == access.tenant_id
            cond2 = target.tenant_id is None or target.tenant_id == access.tenant_id
            cond3 = target.provider_user_id == access.provider_user_id
            logger.info(
                "[DIAG get_services] access.role=%s access.tenant_id=%s access.provider_user_id=%s | "
                "service.enterprise_id=%s service.enterprise.tenant_id=%s service.tenant_id=%s service.provider_user_id=%s | "
                "cond1_enterprise_tenant_matches=%s cond2_service_tenant_ok=%s cond3_provider_matches=%s all_pass=%s | "
                "total_matched=%s",
                access.role, access.tenant_id, access.provider_user_id,
                target.enterprise_id, ent_tenant_id, target.tenant_id, target.provider_user_id,
                cond1, cond2, cond3, cond1 and cond2 and cond3,
                total,
            )

    return items, total


def get_service_by_id(db: Session, service_id: UUID, include_deleted: bool = False, *, access=None):
    query = (
        db.query(Service)
        .options(joinedload(Service.enterprise))
        .filter(Service.id == service_id)
    )
    query = scope_catalog_query(query, Service, access)
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
