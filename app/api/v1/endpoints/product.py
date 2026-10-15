from uuid import UUID
from app.core.catalog_access import get_catalog_access, require_catalog_writer, require_review_moderator
from app.core.dependencies import extract_access_token, get_current_user

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.product_schema import (
    ProductCreate,
    ProductDetailResponse,
    ProductPaginatedResponse,
    ProductResponse,
    ProductReviewCreate,
    ProductReviewListResponse,
    ProductReviewModerateRequest,
    ProductReviewResponse,
    ProductUpdate,
)
from app.services.review_common import ListOptions, review_list_params
from app.services.product_service import (
    create_product_review_service,
    create_product_service,
    delete_product_review_service,
    delete_product_service,
    get_my_product_review_service,
    get_product_service,
    get_products_service,
    list_product_reviews_service,
    moderate_product_review_service,
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
    access=Depends(require_catalog_writer),
):
    return create_product_service(db, product, access=access)


@router.get(
    "/",
    response_model=ProductPaginatedResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Products (assigned listings for Providers)",
    description="Authenticated internal users/Providers are automatically restricted to their assigned listings in their tenant. No provider_user_id query parameter is needed.",
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
    access=Depends(get_catalog_access),
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
        access=access,
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
    access=Depends(get_catalog_access),
):
    return get_product_service(db, product_id, access=access)


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
    access=Depends(require_catalog_writer),
):
    return update_product_service(db, product_id, product, access=access)


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_200_OK,
    summary="Deactivate Product",
)
def delete_product(
    product_id: UUID = Path(..., description="Unique identifier of the product"),
    db: Session = Depends(get_db),
    access=Depends(require_catalog_writer),
):
    delete_product_service(db, product_id, access=access)
    return {"message": "Product marked inactive successfully"}


@router.post(
    "/{product_id}/reviews",
    response_model=ProductReviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit or update your review",
    description=(
        "One review per user per product. A new review is `approved` and public at once. Submitting again edits it and keeps "
        "its status, except that editing a `rejected` review sends it back to `pending` (a resubmission). "
        "`comment` is optional, at most 2000 characters. The answer carries `status_label`, `is_public` and `edit_label`. `is_verified_purchase` is set automatically from your order history."
    ),
)
def create_product_review(
    request: Request,
    payload: ProductReviewCreate,
    product_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return create_product_review_service(db, product_id, payload, current_user, access_token=extract_access_token(request))


@router.get(
    "/{product_id}/reviews",
    response_model=ProductReviewListResponse,
    summary="List reviews with average rating",
    description=(
        "Public — approved reviews only. `average_rating` is null when there are none (show \"No ratings yet\"). "
        "`rating_distribution` is the number of approved reviews per star. Optional `sort` (newest|oldest|highest|lowest), "
        "`rating`, `with_comment`, `page` and `page_size` change which reviews are listed; the average, `total` and "
        "`rating_distribution` always cover all approved reviews."
    ),
)
def list_product_reviews(
    product_id: UUID = Path(...),
    opts: ListOptions = Depends(review_list_params),
    db: Session = Depends(get_db),
):
    return list_product_reviews_service(db, product_id, opts)


@router.get(
    "/{product_id}/reviews/me",
    response_model=ProductReviewResponse | None,
    summary="My review of this product (any status), or null",
)
def get_my_product_review(
    product_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return get_my_product_review_service(db, product_id, current_user)


@router.delete(
    "/{product_id}/reviews/{review_id}",
    summary="Delete a review",
    description=(
        "The review's own author, or a Super Admin. Enterprise Admins and providers cannot delete a customer's review. A Super Admin's delete keeps the review for its author with moderation_status `deleted` (hidden from everyone else)."
    ),
)
def delete_product_review(
    request: Request,
    product_id: UUID = Path(...),
    review_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return delete_product_review_service(
        db, product_id, review_id, current_user, access_token=extract_access_token(request)
    )


@router.patch(
    "/{product_id}/reviews/{review_id}/moderate",
    response_model=ProductReviewResponse,
    summary="Approve, reject, or reset a review's moderation status",
    description=(
        "Super Admin only; Enterprise Admins and providers get 403. Allowed moves: approved -> rejected | pending, "
        "pending -> approved | rejected, rejected -> approved. The same status again does nothing; any other move is 409."
    ),
)
def moderate_product_review(
    request: Request,
    payload: ProductReviewModerateRequest,
    product_id: UUID = Path(...),
    review_id: UUID = Path(...),
    db: Session = Depends(get_db),
    access=Depends(require_review_moderator),
    current_user: dict = Depends(get_current_user),
):
    return moderate_product_review_service(
        db, product_id, review_id, payload.action, access, access_token=extract_access_token(request), actor=current_user
    )
