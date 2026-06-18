from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Path,
    status
)

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
    status_code=status.HTTP_201_CREATED,
    summary="Create Product",
    description="""
Create a new product in the marketplace.

Products belong to an enterprise and can contain dynamic attributes.

Examples:
- Laptop
- Mobile Phone
- Office Chair
- Monitor
""",
    responses={
        201: {"description": "Product created successfully"},
        400: {"description": "Invalid request"},
        422: {"description": "Validation error"},
        500: {"description": "Internal server error"}
    }
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
    status_code=status.HTTP_200_OK,
    summary="Get All Products",
    description="""
Retrieve all active products available in the system.
""",
    responses={
        200: {"description": "Products retrieved successfully"},
        500: {"description": "Internal server error"}
    }
)
def get_products(
    db: Session = Depends(get_db)
):
    return get_products_service(db)


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Product By ID",
    description="""
Retrieve a specific product using its unique identifier.
""",
    responses={
        200: {"description": "Product retrieved successfully"},
        404: {"description": "Product not found"},
        422: {"description": "Validation error"},
        500: {"description": "Internal server error"}
    }
)
def get_product(
    product_id: UUID = Path(
        ...,
        description="Unique identifier of the product"
    ),
    db: Session = Depends(get_db)
):
    return get_product_service(
        db,
        product_id
    )


@router.put(
    "/{product_id}",
    response_model=ProductResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Product",
    description="""
Update an existing product.

Only the supplied fields will be updated.
""",
    responses={
        200: {"description": "Product updated successfully"},
        404: {"description": "Product not found"},
        422: {"description": "Validation error"},
        500: {"description": "Internal server error"}
    }
)
def update_product(
    product: ProductUpdate,
    product_id: UUID = Path(
        ...,
        description="Unique identifier of the product"
    ),
    db: Session = Depends(get_db)
):
    return update_product_service(
        db,
        product_id,
        product
    )


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_200_OK,
    summary="Deactivate Product",
    description="""
Marks a product as inactive.

This operation performs a soft delete and does not permanently remove the record.
""",
    responses={
        200: {"description": "Product marked inactive successfully"},
        404: {"description": "Product not found"},
        422: {"description": "Validation error"},
        500: {"description": "Internal server error"}
    }
)
def delete_product(
    product_id: UUID = Path(
        ...,
        description="Unique identifier of the product"
    ),
    db: Session = Depends(get_db)
):
    delete_product_service(
        db,
        product_id
    )

    return {
        "message": "Product marked inactive successfully"
    }