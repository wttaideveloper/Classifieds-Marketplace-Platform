from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.models.merchant_model import (
    Merchant,
    MerchantProfile,
    MerchantBusinessDraft,
    MerchantListing,
    MerchantListingDraft
)

# MERCHANT 

def get_merchant_by_email(db: Session, email: str):
    return db.query(Merchant).filter(
        Merchant.businessEmail == email
    ).first()

def get_merchant_by_external_auth_id(db: Session, external_auth_user_id: str):
    """
    Optional lookup used by some auth flows.
    If the column isn't present in the model, return None.
    """
    if not hasattr(Merchant, "externalAuthUserId"):
        return None
    return db.query(Merchant).filter(
        Merchant.externalAuthUserId == external_auth_user_id
    ).first()

def get_merchant_by_id(db: Session, merch_id: str):
    return db.query(Merchant).filter(
        Merchant.id == merch_id
    ).first()

def create_merchant(db: Session, merchant: Merchant):
    try:
        db.add(merchant)
        db.commit()
        db.refresh(merchant)
        return merchant
    except Exception:
        db.rollback()
        raise

def update_merchant(db: Session, merchant: Merchant):
    try:
        db.add(merchant)
        db.commit()
        db.refresh(merchant)
        return merchant
    except Exception:
        db.rollback()
        raise

# BUSINESS PROFILE 

def get_business_profile_by_merchant_id(
    db: Session,
    merchant_id: str
):
    return db.query(MerchantProfile).filter(
        MerchantProfile.merchant_id == merchant_id
    ).first()

def create_business_profile(
    db: Session,
    profile: MerchantProfile
):
    try:
        db.add(profile)
        db.commit()
        db.refresh(profile)
        return profile
    except Exception:
        db.rollback()
        raise

def update_business_profile(
    db: Session,
    profile: MerchantProfile
):
    try:
        db.add(profile)
        db.commit()
        db.refresh(profile)
        return profile
    except Exception:
        db.rollback()
        raise

# BUSINESS DRAFT

def create_business_draft(
    db: Session,
    draft: MerchantBusinessDraft
):
    try:
        db.add(draft)
        db.commit()
        db.refresh(draft)
        return draft
    except Exception:
        db.rollback()
        raise

def get_business_draft_by_merchant_id(db: Session, merchant_id: str):
    return db.query(MerchantBusinessDraft).filter(
        MerchantBusinessDraft.merchant_id == merchant_id
    ).first()


def update_business_draft(db: Session, draft: MerchantBusinessDraft):
    try:
        db.add(draft)
        db.commit()
        db.refresh(draft)
        return draft
    except Exception:
        db.rollback()
        raise

def create_listing_repo(db: Session, payload):

    listing = MerchantListing(
        businessId=payload.businessId,
        listingType=payload.listingType,
        title=payload.title,
        description=payload.description,
        categoryId=payload.categoryId,
        subcategoryId=payload.subcategoryId,
        price=payload.price,
        currency=payload.currency,
        images=payload.images,
        status=payload.status,
        tags=payload.tags,

        # Product
        stockQuantity=payload.stockQuantity,
        sku=payload.sku,
        weight=payload.weight,

        # Service
        duration=payload.duration,
        serviceMode=payload.serviceMode,
        availability=payload.availability,

        # Event / Training / Program
        startDate=payload.startDate,
        endDate=payload.endDate,
        capacity=payload.capacity,
        location=payload.location,
        isOnline=payload.isOnline,
        registrationDeadline=payload.registrationDeadline
    )

    db.add(listing)
    db.commit()
    db.refresh(listing)

    return listing


def save_listing_draft_repo(
    db: Session,
    payload
):

    draft = MerchantListingDraft(

        businessId=payload.businessId,
        listingType=payload.listingType,

        title=payload.title,
        description=payload.description,

        categoryId=payload.categoryId,
        subcategoryId=payload.subcategoryId,

        price=payload.price,
        currency=payload.currency,

        images=payload.images,

        status="draft",

        tags=payload.tags,

        # Product
        stockQuantity=payload.stockQuantity,
        sku=payload.sku,
        weight=payload.weight,

        # Service
        duration=payload.duration,
        serviceMode=payload.serviceMode,
        availability=payload.availability,

        # Event / Training / Program
        startDate=payload.startDate,
        endDate=payload.endDate,
        capacity=payload.capacity,
        location=payload.location,
        isOnline=payload.isOnline,
        registrationDeadline=payload.registrationDeadline
    )

    db.add(draft)
    db.commit()
    db.refresh(draft)

    return draft

def get_my_listings_repo(
    db: Session,
    merchant_id,
    businessId,
    status,
    listingType,
    search,
    skip,
    limit
):

    query = db.query(MerchantListing)

    # FILTER BY BUSINESS ID
    if businessId:
        query = query.filter(
            MerchantListing.businessId == businessId
        )

    # FILTER BY STATUS
    if status:
        query = query.filter(
            MerchantListing.status == status
        )

    # FILTER BY LISTING TYPE
    if listingType:
        query = query.filter(
            MerchantListing.listingType == listingType
        )

    # SEARCH
    if search:
        query = query.filter(
            or_(
                MerchantListing.title.ilike(f"%{search}%"),
                MerchantListing.description.ilike(f"%{search}%")
            )
        )
    total = query.count()
    listings = query.order_by(
        MerchantListing.created_at.desc()
    ).offset(skip).limit(limit).all()

    return total, listings

def get_listing_details_repo(
    db: Session,
    listingId
):
    listing = db.query(MerchantListing).filter(
        MerchantListing.id == listingId
    ).first()
    return listing

def get_listing_by_id_repo(
    db: Session,
    listingId
):
    return db.query(MerchantListing).filter(
        MerchantListing.id == listingId
    ).first()


def update_listing_repo(
    db: Session,
    listing,
    payload
):
    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(listing, key, value)
    db.commit()
    db.refresh(listing)
    return listing

def delete_listing_repo(
    db: Session,
    listing
):
    db.delete(listing)
    db.commit()
    return True

def publish_listing_repo(
    db: Session,
    listing
):
    listing.status = "published"
    db.commit()
    db.refresh(listing)
    return listing

def unpublish_listing_repo(
    db: Session,
    listing
):
    listing.status = "draft"
    db.commit()
    db.refresh(listing)
    return listing

def upload_listing_images_repo(
    db: Session,
    listing,
    uploaded_images
):
    existing_images = listing.images or []
    listing.images = existing_images + uploaded_images
    db.commit()
    db.refresh(listing)
    return listing

def delete_listing_image_repo(
    db: Session,
    listing,
    updated_images
):
    listing.images = updated_images
    db.commit()
    db.refresh(listing)
    return listing