from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from math import radians, cos, sin, asin, sqrt

from app.models.admin_model import Business
from app.models.merchant_model import MerchantProfile, MerchantListing


def _haversine(lat1, lon1, lat2, lon2) -> float:
    """Return distance in km between two coordinates."""
    R = 6371
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return R * 2 * asin(sqrt(a))


def search_businesses_repo(
    db: Session,
    keyword: str = None,
    category_id: str = None,
    city: str = None,
    skip: int = 0,
    limit: int = 10,
):
    query = db.query(Business, MerchantProfile).join(
        MerchantProfile,
        Business.merchant_id == MerchantProfile.merchant_id,
        isouter=True
    ).filter(Business.status == "approved")

    if keyword:
        query = query.filter(
            or_(
                Business.name.ilike(f"%{keyword}%"),
                MerchantProfile.businessDescription.ilike(f"%{keyword}%"),
            )
        )

    if category_id:
        query = query.filter(MerchantProfile.primaryCategory == category_id)

    if city:
        query = query.filter(MerchantProfile.city.ilike(f"%{city}%"))

    total = query.count()
    results = query.offset(skip).limit(limit).all()
    return total, results


def search_listings_repo(
    db: Session,
    keyword: str = None,
    category_id: str = None,
    listing_type: str = None,
    min_price: float = None,
    max_price: float = None,
    skip: int = 0,
    limit: int = 10,
):
    query = db.query(MerchantListing, Business).join(
        Business,
        MerchantListing.businessId == Business.id,
        isouter=True
    ).filter(
        MerchantListing.status == "published",
        Business.status == "approved"
    )

    if keyword:
        query = query.filter(
            or_(
                MerchantListing.title.ilike(f"%{keyword}%"),
                MerchantListing.description.ilike(f"%{keyword}%"),
            )
        )

    if category_id:
        query = query.filter(MerchantListing.categoryId == category_id)

    if listing_type:
        query = query.filter(MerchantListing.listingType == listing_type)

    if min_price is not None:
        query = query.filter(MerchantListing.price >= min_price)

    if max_price is not None:
        query = query.filter(MerchantListing.price <= max_price)

    total = query.count()
    results = query.order_by(MerchantListing.created_at.desc()).offset(skip).limit(limit).all()
    return total, results


def nearby_businesses_repo(
    db: Session,
    latitude: float,
    longitude: float,
    radius: float,
    skip: int = 0,
    limit: int = 10,
):
    profiles = db.query(MerchantProfile, Business).join(
        Business,
        MerchantProfile.merchant_id == Business.merchant_id,
        isouter=True
    ).filter(
        MerchantProfile.latitude.isnot(None),
        MerchantProfile.longitude.isnot(None),
        Business.status == "approved"
    ).all()

    nearby = []
    for profile, business in profiles:
        try:
            dist = _haversine(
                latitude, longitude,
                float(profile.latitude), float(profile.longitude)
            )
            if dist <= radius:
                nearby.append((profile, business, dist))
        except (ValueError, TypeError):
            continue

    nearby.sort(key=lambda x: x[2])
    total = len(nearby)
    paginated = nearby[skip: skip + limit]
    return total, paginated
