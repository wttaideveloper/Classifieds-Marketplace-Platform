from uuid import UUID

from fastapi import HTTPException
from fastapi import status

from sqlalchemy.orm import Session

from app.models.enterprise_model import Enterprise

from app.repository.product_repo import (
    create_product,
    get_products,
    get_product_by_id,
    update_product,
    delete_product
)


def create_product_service(
    db: Session,
    product_data
):
    enterprise = (
        db.query(Enterprise)
        .filter(
            Enterprise.id ==
            product_data.enterprise_id
        )
        .first()
    )

    if not enterprise:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enterprise not found"
        )

    return create_product(
        db,
        product_data
    )


def get_products_service(
    db: Session
):
    return get_products(db)


def get_product_service(
    db: Session,
    product_id: UUID
):
    product = get_product_by_id(
        db,
        product_id
    )

    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found"
        )

    return product


def update_product_service(
    db: Session,
    product_id: UUID,
    update_data
):
    product = get_product_by_id(
        db,
        product_id
    )

    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found"
        )

    return update_product(
        db,
        product,
        update_data
    )


def delete_product_service(
    db: Session,
    product_id: UUID
):
    product = get_product_by_id(
        db,
        product_id
    )

    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found"
        )

    return delete_product(
        db,
        product
    )