from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.models.enterprise_model import Enterprise
from app.models.location_model import EnterpriseLocation
from app.models.product_model import Product
from app.models.service_model import Service
from app.repository.query_utils import (
    apply_ilike_search,
    apply_soft_delete_filter,
    build_pagination_meta,
    paginate_query,
)
from app.schemas.enterprise_schema import EnterpriseListItemResponse, EnterprisePaginatedResponse
from app.schemas.product_schema import ProductListItemResponse, ProductPaginatedResponse
from app.schemas.service_schema import ServiceListItemResponse, ServicePaginatedResponse
from app.services.response_mappers import (
    map_enterprise_list_item,
    map_product_list_item,
    map_service_list_item,
)


def search_enterprises_service(
    db: Session,
    *,
    query: str | None = None,
    tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None,
    category: str | None = None,
    city: str | None = None,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> EnterprisePaginatedResponse:
    db_query = db.query(Enterprise).distinct()
    db_query = apply_soft_delete_filter(db_query, Enterprise, False)

    if enterprise_id:
        db_query = db_query.filter(Enterprise.id == enterprise_id)
    if tenant_id:
        db_query = db_query.filter(Enterprise.tenant_id == tenant_id)
    if category:
        db_query = db_query.filter(Enterprise.business_category == category)
    if status_filter:
        db_query = db_query.filter(Enterprise.status == status_filter)
    if city:
        db_query = db_query.outerjoin(
            EnterpriseLocation,
            (EnterpriseLocation.enterprise_id == Enterprise.id)
            & (EnterpriseLocation.is_deleted.is_(False)),
        ).filter(EnterpriseLocation.city.ilike(f"%{city.strip()}%"))
    if query:
        db_query = apply_ilike_search(
            db_query,
            [
                Enterprise.business_short_name,
                Enterprise.business_legal_name,
                Enterprise.business_description,
                Enterprise.business_email,
                Enterprise.business_category,
                Enterprise.tagline,
            ],
            query,
        )

    db_query = db_query.order_by(Enterprise.created_at.desc())
    items, total = paginate_query(db_query, page, page_size)
    return EnterprisePaginatedResponse(
        items=[
            EnterpriseListItemResponse.model_validate(map_enterprise_list_item(item))
            for item in items
        ],
        pagination=build_pagination_meta(total, page, page_size),
    )


def search_products_service(
    db: Session,
    *,
    query: str | None = None,
    tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None,
    category: str | None = None,
    city: str | None = None,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> ProductPaginatedResponse:
    db_query = (
        db.query(Product)
        .options(joinedload(Product.enterprise))
    )
    db_query = apply_soft_delete_filter(db_query, Product, False)

    if tenant_id:
        db_query = db_query.filter(Product.tenant_id == tenant_id)
    if enterprise_id:
        db_query = db_query.filter(Product.enterprise_id == enterprise_id)
    if category:
        db_query = db_query.filter(Product.product_category == category)
    if status_filter:
        db_query = db_query.filter(Product.status == status_filter)
    if city:
        db_query = db_query.outerjoin(
            EnterpriseLocation,
            Product.location_id == EnterpriseLocation.id,
        ).filter(EnterpriseLocation.city.ilike(f"%{city.strip()}%"))
    if query:
        db_query = apply_ilike_search(
            db_query,
            [
                Product.product_name,
                Product.product_description,
                Product.product_category,
                Product.sku,
            ],
            query,
        )

    db_query = db_query.order_by(Product.created_at.desc())
    items, total = paginate_query(db_query, page, page_size)
    return ProductPaginatedResponse(
        items=[
            ProductListItemResponse.model_validate(map_product_list_item(item))
            for item in items
        ],
        pagination=build_pagination_meta(total, page, page_size),
    )


def search_services_service(
    db: Session,
    *,
    query: str | None = None,
    tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None,
    category: str | None = None,
    city: str | None = None,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> ServicePaginatedResponse:
    db_query = (
        db.query(Service)
        .options(joinedload(Service.enterprise))
    )
    db_query = apply_soft_delete_filter(db_query, Service, False)

    if tenant_id:
        db_query = db_query.filter(Service.tenant_id == tenant_id)
    if enterprise_id:
        db_query = db_query.filter(Service.enterprise_id == enterprise_id)
    if category:
        db_query = db_query.filter(Service.service_category == category)
    if status_filter:
        db_query = db_query.filter(Service.status == status_filter)
    if city:
        db_query = db_query.outerjoin(
            EnterpriseLocation,
            Service.location_id == EnterpriseLocation.id,
        ).filter(EnterpriseLocation.city.ilike(f"%{city.strip()}%"))
    if query:
        db_query = apply_ilike_search(
            db_query,
            [
                Service.service_name,
                Service.service_description,
                Service.service_category,
                Service.provider_name,
                Service.instructor_name,
            ],
            query,
        )

    db_query = db_query.order_by(Service.created_at.desc())
    items, total = paginate_query(db_query, page, page_size)
    return ServicePaginatedResponse(
        items=[
            ServiceListItemResponse.model_validate(map_service_list_item(item))
            for item in items
        ],
        pagination=build_pagination_meta(total, page, page_size),
    )
