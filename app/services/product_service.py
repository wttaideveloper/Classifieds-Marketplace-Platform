from uuid import UUID
from app.core.catalog_access import validate_catalog_write

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.enterprise_model import Enterprise
from app.models.location_model import EnterpriseLocation
from app.repository.product_repo import (
    create_product,
    delete_product,
    get_product_by_id,
    get_products,
    update_product,
)
from app.repository.query_utils import build_pagination_meta
from app.services import catalog_reviews
from app.schemas.product_schema import (
    ProductDetailResponse,
    ProductListItemResponse,
    ProductPaginatedResponse,
    ProductResponse,
)
from app.services.response_mappers import (
    map_product_detail,
    map_product_list_item,
    map_product_write,
)


def _validate_references(db: Session, enterprise_id: UUID, location_id: UUID | None):
    enterprise = (
        db.query(Enterprise)
        .filter(
            Enterprise.id == enterprise_id,
            Enterprise.is_deleted.is_(False),
        )
        .first()
    )
    if not enterprise:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enterprise not found",
        )

    if location_id:
        location = (
            db.query(EnterpriseLocation)
            .filter(
                EnterpriseLocation.id == location_id,
                EnterpriseLocation.enterprise_id == enterprise_id,
                EnterpriseLocation.is_deleted.is_(False),
            )
            .first()
        )
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Location not found for this enterprise",
            )


def create_product_service(db: Session, product_data, *, access=None):
    validate_catalog_write(db, product_data.enterprise_id, product_data.tenant_id, access)
    _validate_references(db, product_data.enterprise_id, product_data.location_id)
    return ProductResponse.model_validate(
        map_product_write(create_product(db, product_data))
    )


def _with_review_stats(card: dict, stat: tuple | None) -> dict:
    """The card's rating and review count come from the approved reviews (0 / 0 when there are none)."""
    average, count = stat or (None, 0)
    return {**card, "rating": average or 0, "reviews_count": count}


def get_products_service(
    db: Session,
    *,
    search: str | None = None,
    category: str | None = None,
    tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None,
    location_id: UUID | None = None,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
    access=None,
) -> ProductPaginatedResponse:
    items, total = get_products(
        db,
        search=search,
        category=category,
        tenant_id=tenant_id,
        enterprise_id=enterprise_id,
        location_id=location_id,
        status=status_filter,
        page=page,
        page_size=page_size,
        access=access,
    )
    stats = catalog_reviews.approved_stats(db, "product", [product.id for product in items])
    return ProductPaginatedResponse(
        items=[
            ProductListItemResponse.model_validate(_with_review_stats(map_product_list_item(product), stats.get(product.id)))
            for product in items
        ],
        pagination=build_pagination_meta(total, page, page_size),
    )


def get_product_service(db: Session, product_id: UUID, *, access=None) -> ProductDetailResponse:
    product = get_product_by_id(db, product_id, access=access)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    return ProductDetailResponse.model_validate(map_product_detail(product, db))


def update_product_service(db: Session, product_id: UUID, update_data, *, access=None):
    product = get_product_by_id(db, product_id, include_deleted=True, access=access)
    if not product or product.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    validate_catalog_write(db, product.enterprise_id, update_data.tenant_id, access)
    location_id = update_data.location_id if update_data.location_id is not None else product.location_id
    _validate_references(db, product.enterprise_id, location_id)

    return ProductResponse.model_validate(
        map_product_write(update_product(db, product, update_data))
    )


def delete_product_service(db: Session, product_id: UUID, *, access=None):
    product = get_product_by_id(db, product_id, access=access)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    validate_catalog_write(db, product.enterprise_id, None, access)
    return delete_product(db, product)


def _product_or_404(db: Session, product_id: UUID):
    product = get_product_by_id(db, product_id)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product


def create_product_review_service(db: Session, product_id: UUID, data, current_user: dict, *, access_token: str | None = None):
    from app.models.cart_model import Order, OrderItem

    product = _product_or_404(db, product_id)
    user_id, _ = catalog_reviews.identity(current_user)
    is_verified = (
        db.query(OrderItem)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(Order.user_id == user_id, Order.status == "confirmed", OrderItem.product_id == product_id)
        .first()
        is not None
    )
    return catalog_reviews.upsert_review(
        db, "product", product, data, current_user, is_verified=is_verified, access_token=access_token
    )


def list_product_reviews_service(db: Session, product_id: UUID, opts=None):
    _product_or_404(db, product_id)
    return catalog_reviews.list_approved(db, "product", product_id, opts)


def get_my_product_review_service(db: Session, product_id: UUID, current_user: dict):
    _product_or_404(db, product_id)
    return catalog_reviews.my_review(db, "product", product_id, current_user)


def delete_product_review_service(db: Session, product_id: UUID, review_id: UUID, current_user: dict, *, access_token: str | None = None):
    product = _product_or_404(db, product_id)
    return catalog_reviews.delete_review(db, "product", product, review_id, current_user, access_token=access_token)


def moderate_product_review_service(db: Session, product_id: UUID, review_id: UUID, action: str, access, *, access_token: str | None = None, actor: dict | None = None):
    product = _product_or_404(db, product_id)
    return catalog_reviews.moderate_review(db, "product", product, review_id, action, access, access_token=access_token, actor=actor)
