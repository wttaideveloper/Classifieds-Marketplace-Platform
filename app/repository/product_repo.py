from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.models.product_model import Product
from app.repository.query_utils import (
    apply_ilike_search,
    apply_soft_delete_filter,
    paginate_query,
)


def create_product(db: Session, product_data):
    payload = product_data.to_model_data() if hasattr(product_data, "to_model_data") else product_data.model_dump()
    product = Product(**payload)
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


def get_products(
    db: Session,
    *,
    search: str | None = None,
    category: str | None = None,
    tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None,
    location_id: UUID | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    include_deleted: bool = False,
):
    query = (
        db.query(Product)
        .options(joinedload(Product.enterprise))
    )
    query = apply_soft_delete_filter(query, Product, include_deleted)

    if tenant_id:
        query = query.filter(Product.tenant_id == tenant_id)
    if enterprise_id:
        query = query.filter(Product.enterprise_id == enterprise_id)
    if location_id:
        query = query.filter(Product.location_id == location_id)
    if category:
        query = query.filter(Product.product_category == category)
    if status:
        query = query.filter(Product.status == status)
    if search:
        query = apply_ilike_search(
            query,
            [
                Product.product_name,
                Product.product_description,
                Product.product_category,
                Product.sku,
            ],
            search,
        )

    query = query.order_by(Product.created_at.desc())
    return paginate_query(query, page, page_size)


def get_product_by_id(db: Session, product_id: UUID, include_deleted: bool = False):
    query = (
        db.query(Product)
        .options(joinedload(Product.enterprise))
        .filter(Product.id == product_id)
    )
    if not include_deleted:
        query = apply_soft_delete_filter(query, Product, include_deleted)
    return query.first()


def update_product(db: Session, product, update_data):
    payload = update_data.to_model_data() if hasattr(update_data, "to_model_data") else update_data.model_dump(exclude_unset=True)
    for key, value in payload.items():
        setattr(product, key, value)
    db.commit()
    db.refresh(product)
    return product


def delete_product(db: Session, product):
    product.is_deleted = True
    product.status = "inactive"
    product.product_status = False
    db.commit()
    db.refresh(product)
    return product
