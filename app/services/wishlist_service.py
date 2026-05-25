from sqlalchemy.orm import Session
from uuid import uuid4
from app.models.wishlist_model import Wishlist, WishlistType
from app.models.customer_model import Customer
from app.models.admin_model import Business
from app.models.merchant_model import MerchantListing
from app.repository.wishlist_repo import (
    create_wishlist,
    get_wishlist_by_customer_and_business,
    get_wishlist_by_customer_and_listing,
    get_all_wishlist,
    get_wishlist_by_id,
    delete_wishlist,
    get_business_wishlist,
    get_listing_wishlist
)
from app.exceptions.custom_exception import CustomException

def create_wishlist_service(
    db: Session,
    payload
):

    customer = db.query(Customer).filter(
        Customer.id == payload.customer_id
    ).first()

    if not customer:
        raise CustomException(
            404,
            "Customer not found"
        )

    if payload.wishlist_type == WishlistType.Business:

        business = db.query(Business).filter(
            Business.id == payload.business_id
        ).first()

        if not business:
            raise CustomException(
                404,
                "Business not found"
            )

        existing_wishlist = (
            get_wishlist_by_customer_and_business(
                db,
                payload.customer_id,
                payload.business_id
            )
        )

        if existing_wishlist:
            raise CustomException(
                409,
                "Business already added to wishlist"
            )

    if payload.wishlist_type == WishlistType.Listing:

        listing = db.query(MerchantListing).filter(
            MerchantListing.id == payload.listing_id
        ).first()

        if not listing:
            raise CustomException(
                404,
                "Listing not found"
            )

        existing_wishlist = (
            get_wishlist_by_customer_and_listing(
                db,
                payload.customer_id,
                payload.listing_id
            )
        )

        if existing_wishlist:
            raise CustomException(
                409,
                "Listing already added to wishlist"
            )

    wishlist = Wishlist(
        id=uuid4(),
        customer_id=payload.customer_id,
        business_id=payload.business_id,
        listing_id=payload.listing_id,
        wishlist_type=payload.wishlist_type
    )

    created_wishlist = create_wishlist(
        db,
        wishlist
    )

    return {
        "success": True,
        "message": "Wishlist created successfully",
        "data": {
            "wishlist_id": created_wishlist.id,
            "wishlist_type": created_wishlist.wishlist_type,
            "created_at": created_wishlist.created_at
        }
    }

def get_all_wishlist_service(
    db: Session
):

    wishlist = get_all_wishlist(db)

    if not wishlist:
        raise CustomException(
            404,
            "Wishlist not found"
        )

    return {
        "success": True,
        "message": "Wishlist fetched successfully",
        "data": wishlist
    }

def delete_wishlist_service(
    db: Session,
    wishlist_id
):

    wishlist = get_wishlist_by_id(
        db,
        wishlist_id
    )

    if not wishlist:
        raise CustomException(
            404,
            "Wishlist not found"
        )

    delete_wishlist(
        db,
        wishlist
    )

    return {
        "success": True,
        "message": "Wishlist deleted successfully"
    }

def get_business_wishlist_service(
    db: Session
):

    wishlist = get_business_wishlist(db)

    if not wishlist:
        raise CustomException(
            404,
            "Business wishlist not found"
        )

    return {
        "success": True,
        "message": "Business wishlist fetched successfully",
        "data": wishlist
    }

def get_listing_wishlist_service(
    db: Session,
    page: int,
    size: int
):

    if page <= 0:
        raise CustomException(
            400,
            "Page must be greater than 0"
        )

    if size <= 0:
        raise CustomException(
            400,
            "Size must be greater than 0"
        )

    skip = (page - 1) * size

    wishlist = get_listing_wishlist(
        db,
        skip,
        size
    )

    if not wishlist:
        raise CustomException(
            404,
            "Listing wishlist not found"
        )

    return {
        "success": True,
        "message": "Listing wishlist fetched successfully",
        "total_records": len(wishlist),
        "page": page,
        "size": size,
        "data": wishlist
    }