from uuid import UUID

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


def create_product_service(db: Session, product_data):
    _validate_references(db, product_data.enterprise_id, product_data.location_id)
    return ProductResponse.model_validate(
        map_product_write(create_product(db, product_data))
    )


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
    )
    return ProductPaginatedResponse(
        items=[
            ProductListItemResponse.model_validate(map_product_list_item(product))
            for product in items
        ],
        pagination=build_pagination_meta(total, page, page_size),
    )


def get_product_service(db: Session, product_id: UUID) -> ProductDetailResponse:
    product = get_product_by_id(db, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    return ProductDetailResponse.model_validate(map_product_detail(product))


def update_product_service(db: Session, product_id: UUID, update_data):
    product = get_product_by_id(db, product_id, include_deleted=True)
    if not product or product.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    location_id = update_data.location_id if update_data.location_id is not None else product.location_id
    _validate_references(db, product.enterprise_id, location_id)

    return ProductResponse.model_validate(
        map_product_write(update_product(db, product, update_data))
    )


def delete_product_service(db: Session, product_id: UUID):
    product = get_product_by_id(db, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    return delete_product(db, product)
