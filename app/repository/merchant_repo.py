from fastapi import status
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.models.merchant_model import (
    Merchant,
    MerchantProfile,
    MerchantBusinessDraft,
    MerchantListing,
    MerchantListingDraft,
    MerchantCustomAttribute,
    BusinessAttributeMapping,
    ListingAttributeMapping
)
from app.models.admin_model import Business, Attribute, AttributeOption
import uuid

# MERCHANT 

def get_merchant_by_email(db: Session, email: str):
    return db.query(Merchant).filter(
        Merchant.businessEmail == email
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

def create_listing_repo(db: Session, merchant_id: str, payload):

     # CHECK BUSINESS BELONGS TO MERCHANT
    business = db.query(Business).filter(
        Business.id == payload.businessId,
        Business.merchant_id == uuid.UUID(merchant_id)
    ).first()

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

    # query = db.query(MerchantListing).filter(
    #     MerchantListing.businessId == businessId
    # )
    query = db.query(MerchantListing).join(
        Business,
        MerchantListing.businessId == Business.id
    ).filter(
        Business.merchant_id == merchant_id
    )

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

# GET ATTRIBUTE BY ID
def get_attribute_by_id_repo(
    db: Session,
    attribute_id
):

    return db.query(Attribute).filter(
        Attribute.id == attribute_id
    ).first()


# CHECK EXISTING CUSTOM ATTRIBUTE
def check_existing_custom_attribute_repo(
    db: Session,
    merchant_id,
    attribute_id
):

    return db.query(MerchantCustomAttribute).filter(
        MerchantCustomAttribute.merchant_id == merchant_id,
        MerchantCustomAttribute.attribute_id == attribute_id
    ).first()


# CREATE CUSTOM ATTRIBUTE
def create_custom_attribute_repo(
    db: Session,
    payload
):

    custom_attribute = MerchantCustomAttribute(
        merchant_id=payload.merchant_id,
        attribute_id=payload.attribute_id,
        custom_label=payload.custom_label,
        custom_placeholder=payload.custom_placeholder,
        is_required=payload.is_required,
        default_value=payload.default_value,
        is_active=payload.is_active
    )
    db.add(custom_attribute)
    db.commit()
    db.refresh(custom_attribute)
    return custom_attribute

# GET BUSINESS BY ID
def get_business_by_id_repo(
    db: Session,
    business_id
):
    return db.query(Business).filter(
        Business.id == business_id
    ).first()

# GET ATTRIBUTE BY ID
def get_attribute_by_id_repo(
    db: Session,
    attribute_id
):
    return db.query(Attribute).filter(
        Attribute.id == attribute_id
    ).first()

# CHECK EXISTING BUSINESS ATTRIBUTE
def check_existing_business_attribute_repo(
    db: Session,
    business_id,
    attribute_id
):
    return db.query(BusinessAttributeMapping).filter(
        BusinessAttributeMapping.business_id == business_id,
        BusinessAttributeMapping.attribute_id == attribute_id
    ).first()

# CREATE BUSINESS ATTRIBUTE MAPPING
def create_business_attribute_mapping_repo(
    db: Session,
    business_id,
    payload
):
    business_attribute = BusinessAttributeMapping(
        business_id=business_id,
        attribute_id=payload.attribute_id,
        attribute_value=payload.attribute_value
    )
    db.add(business_attribute)
    db.commit()
    db.refresh(business_attribute)
    return business_attribute

# GET LISTING BY ID
def get_listing_by_id_repo(
    db: Session,
    listing_id
):
    return db.query(MerchantListing).filter(
        MerchantListing.id == listing_id
    ).first()

# CHECK EXISTING LISTING ATTRIBUTE
def check_existing_listing_attribute_repo(
    db: Session,
    listing_id,
    attribute_id
):
    return db.query(ListingAttributeMapping).filter(
        ListingAttributeMapping.listing_id == listing_id,
        ListingAttributeMapping.attribute_id == attribute_id
    ).first()

# CREATE LISTING ATTRIBUTE MAPPING
def create_listing_attribute_mapping_repo(
    db: Session,
    listing_id,
    payload
):
    listing_attribute = ListingAttributeMapping(
        listing_id=listing_id,
        attribute_id=payload.attribute_id,
        attribute_value=payload.attribute_value
    )
    db.add(listing_attribute)
    db.commit()
    db.refresh(listing_attribute)
    return listing_attribute