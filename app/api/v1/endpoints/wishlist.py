from fastapi import APIRouter, Depends, status, Query
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.schemas.wishlist_schema import (
    WishlistCreate,
    WishlistResponse,
    WishlistListResponse,
    WishlistDeleteResponse,
    BusinessWishlistListResponse,
    ListingWishlistListResponse
)
from app.services.wishlist_service import (
    create_wishlist_service, 
    get_all_wishlist_service, 
    delete_wishlist_service, 
    get_business_wishlist_service,
    get_listing_wishlist_service
)
from uuid import UUID

router = APIRouter(
    tags=["Wishlist"]
)

@router.post(
    "/",
    response_model=WishlistResponse,
    status_code=status.HTTP_201_CREATED
)
def create_wishlist(
    payload: WishlistCreate,
    db: Session = Depends(get_db)
):
    return create_wishlist_service(
        db=db,
        payload=payload
    )

@router.get(
    "/",
    response_model=WishlistListResponse,
    status_code=status.HTTP_200_OK
)
def get_all_wishlist(
    db: Session = Depends(get_db)
):
    return get_all_wishlist_service(db)

@router.delete(
    "/{wishlist_id}",
    response_model=WishlistDeleteResponse,
    status_code=status.HTTP_200_OK
)
def delete_wishlist_api(
    wishlist_id: UUID,
    db: Session = Depends(get_db)
):
    return delete_wishlist_service(
        db,
        wishlist_id
    )

@router.get(
    "/businesses",
    response_model=BusinessWishlistListResponse,
    status_code=status.HTTP_200_OK
)
def get_business_wishlist_api(
    db: Session = Depends(get_db)
):
    return get_business_wishlist_service(db)

@router.get(
    "/listings",
    response_model=ListingWishlistListResponse,
    status_code=status.HTTP_200_OK
)
def get_listing_wishlist_api(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db)
):
    return get_listing_wishlist_service(
        db,
        page,
        size
    )