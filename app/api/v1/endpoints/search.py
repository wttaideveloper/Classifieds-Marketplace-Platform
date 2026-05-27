from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.services.search_service import (
    search_businesses_service,
    search_listings_service,
    nearby_businesses_service,
)

router = APIRouter(tags=["Search & Discovery"])


@router.get("/search/business")
def search_businesses(
    keyword: str = Query(None, examples=["dental"]),
    category_id: str = Query(None, examples=[None]),
    subcategory_id: str = Query(None, examples=[None]),
    city: str = Query(None, examples=["Mumbai"]),
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return search_businesses_service(
        db=db,
        keyword=keyword,
        category_id=category_id,
        subcategory_id=subcategory_id,
        city=city,
        page=page,
        size=size,
    )


@router.get("/search/listings")
def search_listings(
    keyword: str = Query(None, examples=["course"]),
    category_id: str = Query(None, examples=[None]),
    subcategory_id: str = Query(None, examples=[None]),
    listing_type: str = Query(None, examples=[None]),
    city: str = Query(None, examples=[None]),
    min_price: float = Query(None, examples=[None]),
    max_price: float = Query(None, examples=[None]),
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return search_listings_service(
        db=db,
        keyword=keyword,
        category_id=category_id,
        subcategory_id=subcategory_id,
        listing_type=listing_type,
        city=city,
        min_price=min_price,
        max_price=max_price,
        page=page,
        size=size,
    )


@router.get("/search/nearby")
def nearby_businesses(
    latitude: float = Query(..., ge=-90, le=90, examples=[13.08]),
    longitude: float = Query(..., ge=-180, le=180, examples=[80.27]),
    radius: float = Query(..., gt=0, examples=[10]),
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return nearby_businesses_service(
        db=db,
        latitude=latitude,
        longitude=longitude,
        radius=radius,
        page=page,
        size=size,
    )
