from uuid import UUID

from fastapi import APIRouter
from fastapi import Depends
from fastapi import status

from sqlalchemy.orm import Session

from app.db.database import get_db

from app.schemas.product_schema import (
    ProductCreate,
    ProductUpdate,
    ProductResponse
)

from app.services.product_service import (
    create_product_service,
    get_products_service,
    get_product_service,
    update_product_service,
    delete_product_service
)

router = APIRouter(
    tags=["Products"]
)


@router.post(
    "/",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED
)
def create_product(
    product: ProductCreate,
    db: Session = Depends(get_db)
):
    return create_product_service(
        db,
        product
    )


@router.get(
    "/",
    response_model=list[ProductResponse],
    status_code=status.HTTP_200_OK
)
def get_products(
    db: Session = Depends(get_db)
):
    return get_products_service(db)


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    status_code=status.HTTP_200_OK
)
def get_product(
    product_id: UUID,
    db: Session = Depends(get_db)
):
    return get_product_service(
        db,
        product_id
    )


@router.put(
    "/{product_id}",
    response_model=ProductResponse,
    status_code=status.HTTP_200_OK
)
def update_product(
    product_id: UUID,
    product: ProductUpdate,
    db: Session = Depends(get_db)
):
    return update_product_service(
        db,
        product_id,
        product
    )


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_200_OK
)
def delete_product(
    product_id: UUID,
    db: Session = Depends(get_db)
):
    delete_product_service(
        db,
        product_id
    )

    return {
        "message":
        "Product marked inactive successfully"
    }