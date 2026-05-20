from fastapi import status
from sqlalchemy.orm import Session
from sqlalchemy import desc
from sqlalchemy import or_
from app.models.merchant_model import (
    Merchant,
    MerchantProfile,
    MerchantBusinessDraft,
    MerchantListing,
    MerchantListingDraft,
    MerchantCustomAttribute,
    BusinessAttributeMapping,
    ListingAttributeMapping,
    BookingStatusHistory
)
from app.schemas.merchant_schema import (
    BookingStatus,
    BookingStatusUpdate
)
from app.models.admin_model import Business, Attribute, AttributeOption
from app.models.customer_model import Customer, Booking
from app.exceptions.custom_exception import CustomException
import uuid
from uuid import UUID

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

def get_merchant_bookings_repo(
    db: Session,
    page: int,
    size: int,
    booking_status: str = None,
    booking_date=None
):

    try:

        query = db.query(
            Booking.id.label("booking_id"),

            (
                Customer.firstName + " " + Customer.lastName
            ).label("customer_name"),

            MerchantListing.title.label("listing_name"),

            Booking.booking_status,

            Booking.booking_date

        ).join(
            Customer,
            Booking.customer_id == Customer.id
        ).join(
            MerchantListing,
            Booking.listing_id == MerchantListing.id
        )

        if booking_status:

            booking_status = booking_status.capitalize()

            query = query.filter(
                Booking.booking_status == BookingStatus(
                    booking_status
                )
            )

        if booking_date:

            query = query.filter(
                Booking.booking_date == booking_date
            )

        total_records = query.count()

        bookings = query.order_by(
            desc(Booking.created_at)
        ).offset(
            (page - 1) * size
        ).limit(size).all()

        return {
            "total_records": total_records,
            "page": page,
            "size": size,
            "bookings": bookings
        }

    except Exception as e:

        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Failed to fetch merchant bookings: {str(e)}"
        )
    
def update_booking_status_repo(
    db: Session,
    booking_id: UUID,
    payload: BookingStatusUpdate
):
    try:
        booking = db.query(Booking).filter(
            Booking.id == booking_id
        ).first()

        if not booking:
            raise CustomException(
                status.HTTP_404_NOT_FOUND,
                "Booking not found"
            )
        current_status = booking.booking_status
        new_status = payload.booking_status
        allowed_transitions = {
            "Pending": ["Approved", "Rejected"],
            "Approved": ["Completed", "Cancelled"]
        }
        if current_status not in allowed_transitions:
            raise CustomException(
                status.HTTP_400_BAD_REQUEST,
                f"Cannot change booking status from {current_status}"
            )
        if new_status not in allowed_transitions[current_status]:
            raise CustomException(
                status.HTTP_400_BAD_REQUEST,
                f"Invalid status transition from {current_status} to {new_status}"
            )
        history = BookingStatusHistory(
            booking_id=booking.id,
            old_status=current_status,
            new_status=new_status,
            updated_by=None,
            remarks=payload.remarks
        )
        booking.booking_status = new_status
        db.add(history)
        db.commit()
        db.refresh(booking)
        return {
            "message": "Booking status updated successfully",
            "booking_id": str(booking.id),
            "old_status": current_status,
            "new_status": new_status
        }
    except CustomException:
        raise
    except Exception as e:
        db.rollback()
        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Failed to update booking status: {str(e)}"
        )