from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import or_

from typing import Optional, Tuple, List
from datetime import datetime

from app.models.merchant_model import (
    Merchant,
    MerchantListing
)

from app.models.admin_model import (
    Admin,
    Business,
    Attribute,
    AttributeOption
)

from app.models.category_model import Category


# CONSTANTS
class BusinessStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUSPENDED = "suspended"

# ADMIN QUERIES
def get_admin_by_id(
    db: Session,
    admin_id
) -> Optional[Admin]:
    return db.query(Admin).filter(
        Admin.id == admin_id
    ).first()

def get_admin_by_email(
    db: Session,
    email: str
) -> Optional[Admin]:
    return db.query(Admin).filter(
        Admin.email == email
    ).first()

def update_admin(
    db: Session,
    admin: Admin
) -> Admin:
    try:
        db.add(admin)
        db.commit()
        db.refresh(admin)
        return admin
    except SQLAlchemyError as e:
        db.rollback()
        raise Exception(
            f"Database Error: {str(e)}"
        )

# BUSINESS LIST
def get_all_businesses(
    db: Session,
    search: Optional[str] = None,
    status: Optional[str] = None,
    category: Optional[str] = None,
    skip: int = 0,
    limit: int = 10
) -> Tuple[int, List[Business]]:
    query = db.query(Business).filter(
        Business.is_deleted  == False
    )

    # SEARCH
    if search:

        query = query.filter(
            or_(
                Business.name.ilike(f"%{search}%"),
                Business.category.ilike(f"%{search}%")
            )
        )

    # STATUS FILTER
    if status:

        query = query.filter(
            Business.status == status.lower()
        )

    # CATEGORY FILTER
    if category:

        query = query.filter(
            Business.category == category
        )

    total = query.count()

    businesses = query.order_by(
        Business.created_at.desc()
    ).offset(skip).limit(limit).all()

    return total, businesses

# BUSINESS DETAIL
def get_business_by_id(
    db: Session,
    business_id
) -> Optional[Business]:

    return db.query(Business).filter(
        Business.id == business_id,
        Business.is_deleted  == False
    ).first()


def get_business_with_merchant(
    db: Session,
    business_id
) -> Optional[Business]:

    return db.query(Business).options(
        joinedload(Business.merchant)
    ).filter(
        Business.id == business_id,
        Business.is_deleted  == False
    ).first()


# BUSINESS STATUS ACTIONS
def approve_business(
    db: Session,
    business: Business
) -> Business:

    business.status = BusinessStatus.APPROVED
    business.approved_at = datetime.utcnow()

    return _commit(
        db=db,
        instance=business
    )


def reject_business(
    db: Session,
    business: Business,
    reason: Optional[str] = None
) -> Business:

    business.status = BusinessStatus.REJECTED
    business.rejection_reason = reason
    business.rejected_at = datetime.utcnow()

    return _commit(
        db=db,
        instance=business
    )


def suspend_business(
    db: Session,
    business: Business,
    reason: Optional[str] = None
) -> Business:

    business.status = BusinessStatus.SUSPENDED
    business.suspension_reason = reason
    business.suspended_at = datetime.utcnow()

    return _commit(
        db=db,
        instance=business
    )


def reactivate_business(
    db: Session,
    business: Business
) -> Business:

    business.status = BusinessStatus.APPROVED
    business.suspension_reason = None
    business.suspended_at = None

    return _commit(
        db=db,
        instance=business
    )


# =========================================================
# INTERNAL COMMIT HELPER
# =========================================================
def _commit(
    db: Session,
    instance
):
    try:
        db.add(instance)
        db.commit()
        db.refresh(instance)
        return instance
    except SQLAlchemyError as e:
        db.rollback()
        raise Exception(
            f"Database Error: {str(e)}"
        )

# GET ALL LISTINGS
def get_all_listings_repo(
    db: Session
):
    listings = db.query(MerchantListing).order_by(
        MerchantListing.created_at.desc()
    ).all()
    total = len(listings)
    return total, listings

# GET LISTING BY ID
def get_listing_by_id_repo(
    db: Session,
    listing_id
):
    return db.query(MerchantListing).filter(
        MerchantListing.id == listing_id
    ).first()

# APPROVE LISTING
def approve_listing_repo(
    db: Session,
    listing
):
    listing.status = "approved"
    listing.approved_at = datetime.utcnow()
    db.commit()
    db.refresh(listing)
    return listing

# REJECT LISTING
def reject_listing_repo(
    db: Session,
    listing,
    reason
):
    listing.status = "rejected"
    listing.rejection_reason = reason
    listing.rejected_at = datetime.utcnow()
    db.commit()
    db.refresh(listing)
    return listing

# SUSPEND LISTING
def suspend_listing_repo(
    db: Session,
    listing,
    reason
):
    listing.status = "suspended"
    listing.suspended_at = datetime.utcnow()
    listing.suspension_reason = reason
    db.commit()
    db.refresh(listing)
    return listing

# REACTIVATE LISTING
def reactivate_listing_repo(
    db: Session,
    listing
):
    listing.status = "approved"
    listing.suspended_at = None
    listing.suspension_reason = None
    db.commit()
    db.refresh(listing)
    return listing

# GET CATEGORY BY NAME
def get_category_by_name_repo(
    db: Session,
    name: str
):

    return db.query(Category).filter(
        Category.name.ilike(name),
        Category.is_deleted == False
    ).first()

# GET CATEGORY BY ID
def get_category_by_id_repo(
    db: Session,
    category_id
):
    return db.query(Category).filter(
        Category.id == category_id,
        Category.is_deleted == False
    ).first()

# CREATE CATEGORY
def create_category_repo(
    db: Session,
    payload
):

    category = Category(
        name=payload.name,
        description=payload.description,
        icon=payload.icon,
        is_active=payload.is_active
    )
    db.add(category)
    db.commit()
    db.refresh(category)
    return category

# CREATE ATTRIBUTE
def create_attribute_repo(
    db: Session,
    payload
):

    attribute = Attribute(
        name=payload.name,
        display_name=payload.display_name,
        slug=payload.slug,
        field_type=payload.field_type,
        placeholder=payload.placeholder,
        is_required=payload.is_required,
        is_active=payload.is_active,
        is_global=payload.is_global,
        created_by=payload.created_by
    )
    db.add(attribute)
    db.commit()
    db.refresh(attribute)

    # CREATE OPTIONS
    if payload.options:

        for option in payload.options:

            attribute_option = AttributeOption(
                attribute_id=attribute.id,
                option_label=option.option_label,
                option_value=option.option_value
            )

            db.add(attribute_option)
        db.commit()
    db.refresh(attribute)
    return attribute

# GET ATTRIBUTE BY SLUG
def get_attribute_by_slug_repo(
    db: Session,
    slug: str
):
    return db.query(Attribute).filter(
        Attribute.slug == slug
    ).first()

# GET ATTRIBUTE BY ID
def get_attribute_by_id_repo(
    db: Session,
    attribute_id
):
    return db.query(Attribute).filter(
        Attribute.id == attribute_id
    ).first()

# GET ALL ATTRIBUTES
def get_all_attributes_repo(db: Session):

    return db.query(Attribute).all()

# UPDATE ATTRIBUTE
def update_attribute_repo(
    db: Session,
    attribute,
    payload
):

    if payload.name is not None:
        attribute.name = payload.name

    if payload.display_name is not None:
        attribute.display_name = payload.display_name

    if payload.placeholder is not None:
        attribute.placeholder = payload.placeholder

    if payload.is_required is not None:
        attribute.is_required = payload.is_required

    if payload.is_active is not None:
        attribute.is_active = payload.is_active
    db.commit()
    db.refresh(attribute)
    return attribute

# DELETE ATTRIBUTE
def delete_attribute_repo(
    db: Session,
    attribute
):
    db.delete(attribute)
    db.commit()
