"""Catalog reads the booking service needs, on its read-only replicas (copied from
app/repository/service_repo.py: get_service_by_id and app/core/catalog_access.py: scope_catalog_query)."""
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from booking_service.models.enterprise_model import Enterprise
from booking_service.models.service_model import Service
from booking_service.repository.query_utils import apply_soft_delete_filter
from mskit.auth.access import CatalogAccess


def scope_catalog_query(query, model, access: CatalogAccess | None):
    if access is None or access.role in ("public", "customer", "super_admin"):
        return query
    query = query.filter(
        model.enterprise.has(Enterprise.tenant_id == access.tenant_id),
        or_(model.tenant_id.is_(None), model.tenant_id == access.tenant_id),
    )
    if access.role == "provider":
        query = query.filter(model.provider_user_id == access.provider_user_id)
    return query


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

