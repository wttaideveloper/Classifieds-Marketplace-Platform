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
    return ProductPaginatedResponse(
        items=[
            ProductListItemResponse.model_validate(map_product_list_item(product))
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


def _current_user_identity(current_user: dict) -> tuple[UUID, str | None]:
    user_id = current_user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authenticated user required")
    try:
        user_id = UUID(str(user_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Authenticated user required")
    name = current_user.get("name") or current_user.get("email")
    return user_id, name


def create_product_review_service(db: Session, product_id: UUID, data, current_user: dict):
    from app.models.cart_model import Order, OrderItem
    from app.models.product_model import ProductReview

    product = get_product_by_id(db, product_id)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    user_id, reviewer_name = _current_user_identity(current_user)
    is_verified = (
        db.query(OrderItem)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(Order.user_id == user_id, Order.status == "confirmed", OrderItem.product_id == product_id)
        .first()
        is not None
    )

    existing = db.query(ProductReview).filter(ProductReview.product_id == product_id, ProductReview.user_id == user_id).first()
    if existing:
        existing.rating = str(data.rating)
        existing.comment = data.comment
        existing.reviewer_name = reviewer_name
        existing.is_verified_purchase = is_verified
        db.commit()
        db.refresh(existing)
        return existing

    review = ProductReview(
        product_id=product_id, user_id=user_id, reviewer_name=reviewer_name,
        rating=str(data.rating), comment=data.comment, is_verified_purchase=is_verified,
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


def list_product_reviews_service(db: Session, product_id: UUID):
    from app.models.product_model import ProductReview

    product = get_product_by_id(db, product_id)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    rows = (
        db.query(ProductReview)
        .filter(ProductReview.product_id == product_id, ProductReview.moderation_status == "approved")
        .order_by(ProductReview.created_at.desc())
        .all()
    )
    average = round(sum(int(r.rating) for r in rows) / len(rows), 2) if rows else None
    return {"reviews": rows, "average_rating": average, "total": len(rows)}


def delete_product_review_service(db: Session, product_id: UUID, review_id: UUID, current_user: dict):
    from app.models.product_model import ProductReview

    user_id, _ = _current_user_identity(current_user)
    review = db.query(ProductReview).filter(ProductReview.id == review_id, ProductReview.product_id == product_id).first()
    if not review:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
    is_owner = review.user_id == user_id
    is_staff = str(current_user.get("role") or "").lower() in ("admin", "provider", "super_admin")
    if not is_owner and not is_staff:
        raise HTTPException(status_code=403, detail="You can only delete your own review")
    db.delete(review)
    db.commit()
    return {"message": "Review deleted"}


def moderate_product_review_service(db: Session, review_id: UUID, action: str):
    from app.models.product_model import ProductReview

    allowed = {"approved", "rejected", "pending"}
    if action not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid moderation action. Allowed: {sorted(allowed)}")
    review = db.query(ProductReview).filter(ProductReview.id == review_id).first()
    if not review:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
    review.moderation_status = action
    db.commit()
    db.refresh(review)
    return review
