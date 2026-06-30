from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.product_schema import (
    ProductCreate,
    ProductDetailResponse,
    ProductPaginatedResponse,
    ProductResponse,
    ProductUpdate,
)
from app.services.product_service import (
    create_product_service,
    delete_product_service,
    get_product_service,
    get_products_service,
    update_product_service,
)

router = APIRouter(tags=["Products"])


@router.post(
    "/",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Product",
)
def create_product(
    product: ProductCreate,
    db: Session = Depends(get_db),
):
    return create_product_service(db, product)


@router.get(
    "/",
    response_model=ProductPaginatedResponse,
    status_code=status.HTTP_200_OK,
    summary="Get All Products",
)
def get_products(
    search: str | None = Query(None, description="Search across product fields."),
    category: str | None = Query(None, description="Filter by category."),
    tenant_id: UUID | None = Query(None, description="Filter by tenant ID."),
    enterprise_id: UUID | None = Query(None, description="Filter by enterprise ID."),
    location_id: UUID | None = Query(None, description="Filter by location ID."),
    status_filter: str | None = Query(None, alias="status", description="Filter by status."),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
):
    return get_products_service(
        db,
        search=search,
        category=category,
        tenant_id=tenant_id,
        enterprise_id=enterprise_id,
        location_id=location_id,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{product_id}",
    response_model=ProductDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Product By ID",
)
def get_product(
    product_id: UUID = Path(..., description="Unique identifier of the product"),
    db: Session = Depends(get_db),
):
    return get_product_service(db, product_id)


@router.put(
    "/{product_id}",
    response_model=ProductResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Product",
)
def update_product(
    product: ProductUpdate,
    product_id: UUID = Path(..., description="Unique identifier of the product"),
    db: Session = Depends(get_db),
):
    return update_product_service(db, product_id, product)


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_200_OK,
    summary="Deactivate Product",
)
def delete_product(
    product_id: UUID = Path(..., description="Unique identifier of the product"),
    db: Session = Depends(get_db),
):
    delete_product_service(db, product_id)
    return {"message": "Product marked inactive successfully"}
