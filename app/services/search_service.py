from math import ceil
from sqlalchemy.orm import Session

from app.exceptions.custom_exception import CustomException
from app.repository.search_repo import (
    search_businesses_repo,
    search_listings_repo,
    nearby_businesses_repo,
)


def _paginate(total, page, size):
    return {
        "page": page,
        "size": size,
        "total_elements": total,
        "total_pages": ceil(total / size) if size else 0,
    }


def search_businesses_service(
    db: Session,
    keyword: str = None,
    category_id: str = None,
    subcategory_id: str = None,
    city: str = None,
    page: int = 1,
    size: int = 10,
):
    try:
        skip = (page - 1) * size
        total, results = search_businesses_repo(
            db=db,
            keyword=keyword,
            category_id=category_id,
            subcategory_id=subcategory_id,
            city=city,
            skip=skip,
            limit=size,
        )
        data = []
        for business, profile in results:
            data.append({
                "business_id": str(business.id),
                "business_name": business.name,
                "category": profile.primaryCategory if profile else None,
                "location": f"{profile.city}, {profile.state}" if profile else None,
                "rating": None,
                "image_url": profile.businessLogo if profile else None,
            })
        return {"success": True, **_paginate(total, page, size), "data": data}
    except Exception as e:
        raise CustomException(500, str(e))


def search_listings_service(
    db: Session,
    keyword: str = None,
    category_id: str = None,
    subcategory_id: str = None,
    listing_type: str = None,
    city: str = None,
    min_price: float = None,
    max_price: float = None,
    page: int = 1,
    size: int = 10,
):
    try:
        skip = (page - 1) * size
        total, results = search_listings_repo(
            db=db,
            keyword=keyword,
            category_id=category_id,
            subcategory_id=subcategory_id,
            listing_type=listing_type,
            city=city,
            min_price=min_price,
            max_price=max_price,
            skip=skip,
            limit=size,
        )
        data = []
        for listing, business in results:
            data.append({
                "listing_id": str(listing.id),
                "title": listing.title,
                "price": listing.price,
                "business_id": str(business.id) if business else None,
                "business_name": business.name if business else None,
                "listing_type": listing.listingType,
                "image_url": listing.images[0] if listing.images else None,
            })
        return {"success": True, **_paginate(total, page, size), "data": data}
    except Exception as e:
        raise CustomException(500, str(e))


def nearby_businesses_service(
    db: Session,
    latitude: float,
    longitude: float,
    radius: float,
    page: int = 1,
    size: int = 10,
):
    try:
        skip = (page - 1) * size
        total, results = nearby_businesses_repo(
            db=db,
            latitude=latitude,
            longitude=longitude,
            radius=radius,
            skip=skip,
            limit=size,
        )
        data = []
        for profile, business, distance in results:
            data.append({
                "business_id": str(business.id) if business else None,
                "business_name": business.name if business else profile.businessName,
                "distance": round(distance, 2),
                "address": profile.fullAddress,
                "category": profile.primaryCategory,
            })
        return {"success": True, **_paginate(total, page, size), "data": data}
    except Exception as e:
        raise CustomException(500, str(e))
