from uuid import UUID
from sqlalchemy.orm import Session

from app.models.product_model import Product


def create_product(
    db: Session,
    product_data
):
    product = Product(
        **product_data.dict()
    )

    db.add(product)
    db.commit()
    db.refresh(product)

    return product


def get_products(db: Session):
    return db.query(Product).all()


def get_product_by_id(
    db: Session,
    product_id: UUID
):
    return (
        db.query(Product)
        .filter(Product.id == product_id)
        .first()
    )


def update_product(
    db: Session,
    product,
    update_data
):
    for key, value in (
        update_data.dict(
            exclude_unset=True
        ).items()
    ):
        setattr(product, key, value)

    db.commit()
    db.refresh(product)

    return product


def delete_product(
    db: Session,
    product
):
    product.product_status = False

    db.commit()
    db.refresh(product)

    return product