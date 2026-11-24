# v2: event/training/program search moved to the events and learning services (they own that data).
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from catalog_service.models.enterprise_model import Enterprise
from catalog_service.models.location_model import EnterpriseLocation
from catalog_service.models.product_model import Product
from catalog_service.models.service_model import Service
from catalog_service.repository.query_utils import (
    apply_ilike_search,
    apply_soft_delete_filter,
    build_pagination_meta,
    paginate_query,
)
from catalog_service.schemas.enterprise_schema import EnterpriseListItemResponse, EnterprisePaginatedResponse
from catalog_service.schemas.product_schema import ProductListItemResponse, ProductPaginatedResponse
from catalog_service.schemas.service_schema import ServiceListItemResponse, ServicePaginatedResponse
from catalog_service.services import catalog_reviews
from catalog_service.services.response_mappers import (
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
    latitude: float | None = None,
    longitude: float | None = None,
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
            EnterpriseListItemResponse.model_validate(
                map_enterprise_list_item(
                    item, db, user_lat=latitude, user_lng=longitude
                )
            )
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
    stats = catalog_reviews.approved_stats(db, "product", [item.id for item in items])
    return ProductPaginatedResponse(
        items=[
            ProductListItemResponse.model_validate(_with_review_stats(map_product_list_item(item), stats.get(item.id)))
            for item in items
        ],
        pagination=build_pagination_meta(total, page, page_size),
    )


def _with_review_stats(card: dict, stat: tuple | None) -> dict:
    """Rating and review count from the approved reviews (0 / 0 when there are none)."""
    average, count = stat or (None, 0)
    return {**card, "rating": average or 0, "reviews_count": count}


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
    stats = catalog_reviews.approved_stats(db, "service", [item.id for item in items])
    return ServicePaginatedResponse(
        items=[
            ServiceListItemResponse.model_validate(_with_review_stats(map_service_list_item(item), stats.get(item.id)))
            for item in items
        ],
        pagination=build_pagination_meta(total, page, page_size),
    )





