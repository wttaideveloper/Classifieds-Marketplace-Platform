import logging
from uuid import UUID
from app.core.catalog_access import scope_catalog_query

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.models.enterprise_model import Enterprise
from app.models.service_model import Service
from app.repository.query_utils import (
    apply_ilike_search,
    apply_soft_delete_filter,
    paginate_query,
)

logger = logging.getLogger(__name__)


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
    # investigation is closed. No tokens/PII/row dumps: aggregate COUNTs run
    # as SEPARATE read-only queries (the real `query` above is untouched),
    # incrementally applying the same conditions scope_catalog_query uses, so
    # we can see exactly which condition zeroes out the result for THIS
    # request's actual resolved access values. Generic — no hardcoded ids.
    if access is not None and access.role == "provider":
        try:
            enterprise_tenant_match_count = (
                db.query(func.count(Service.id))
                .filter(Service.enterprise.has(Enterprise.tenant_id == access.tenant_id))
                .scalar()
            )
            service_tenant_ok_count = (
                db.query(func.count(Service.id))
                .filter(
                    Service.enterprise.has(Enterprise.tenant_id == access.tenant_id),
                    or_(Service.tenant_id.is_(None), Service.tenant_id == access.tenant_id),
                )
                .scalar()
            )
            provider_match_count = (
                db.query(func.count(Service.id))
                .filter(
                    Service.enterprise.has(Enterprise.tenant_id == access.tenant_id),
                    or_(Service.tenant_id.is_(None), Service.tenant_id == access.tenant_id),
                    Service.provider_user_id == access.provider_user_id,
                )
                .scalar()
            )
            logger.info(
                "[DIAG get_services SQL-filter] access.role=%s access.tenant_id=%s access.provider_user_id=%s | "
                "enterprise_tenant_match_count=%s service_tenant_ok_count=%s provider_match_count=%s | "
                "final_total_matched=%s",
                access.role, access.tenant_id, access.provider_user_id,
                enterprise_tenant_match_count, service_tenant_ok_count, provider_match_count,
                total,
            )
        except Exception:
            logger.debug("[DIAG get_services SQL-filter] diagnostic logging failed", exc_info=True)

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
