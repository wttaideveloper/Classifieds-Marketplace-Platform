from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import or_
from typing import Optional, Tuple, List
from uuid import UUID
from datetime import datetime
from app.models.customer_model import PublicListing, Category
from app.models.admin_model import Admin, Business


# CONSTANTS

class BusinessStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUSPENDED = "suspended"

# ADMIN QUERIES

def get_admin_by_id(db: Session, admin_id: int) -> Optional[Admin]:
    return db.query(Admin).filter(Admin.id == admin_id).first()


def get_admin_by_email(db: Session, email: str) -> Optional[Admin]:
    return db.query(Admin).filter(Admin.email == email).first()


def get_admin_by_reset_token(db: Session, token: str) -> Optional[Admin]:
    return db.query(Admin).filter(Admin.reset_token == token).first()


def update_admin(db: Session, admin: Admin) -> Admin:
    try:
        db.add(admin)
        db.commit()
        db.refresh(admin)
        return admin
    except SQLAlchemyError as e:
        db.rollback()
        raise Exception(f"Database error: {str(e)}")

# BUSINESS - LIST


def get_all_businesses(
    db: Session,
    search: Optional[str] = None,
    status: Optional[str] = None,
    category: Optional[str] = None,
    skip: int = 0,
    limit: int = 10
) -> Tuple[int, List[Business]]:

    query = db.query(Business).filter(
        Business.is_deleted.is_(False)
    )

    # SEARCH (model fields only)
    if search:
        query = query.filter(
            or_(
                Business.name.ilike(f"%{search}%"),
                Business.category.ilike(f"%{search}%")
            )
        )

    # STATUS FILTER (exact match)
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

# BUSINESS - DETAIL

def get_business_by_id(db: Session, business_id: UUID) -> Optional[Business]:
    return db.query(Business).filter(
        Business.id == business_id,
        Business.is_deleted.is_(False)
    ).first()


def get_business_with_merchant(db: Session, business_id: UUID) -> Optional[Business]:
    return db.query(Business).options(
        joinedload(Business.merchant)
    ).filter(
        Business.id == business_id,
        Business.is_deleted.is_(False)
    ).first()

# BUSINESS STATUS ACTIONS

def approve_business(db: Session, business: Business) -> Business:
    if business.status != BusinessStatus.APPROVED:
        business.status = BusinessStatus.APPROVED
        business.approved_at = datetime.utcnow()

    return _commit(db, business)


def reject_business(
    db: Session,
    business: Business,
    reason: Optional[str] = None
) -> Business:

    business.status = BusinessStatus.REJECTED
    business.rejection_reason = reason
    business.rejected_at = datetime.utcnow()

    return _commit(db, business)


def suspend_business(
    db: Session,
    business: Business,
    reason: Optional[str] = None
) -> Business:

    business.status = BusinessStatus.SUSPENDED
    business.suspension_reason = reason
    business.suspended_at = datetime.utcnow()

    return _commit(db, business)


def reactivate_business(db: Session, business: Business) -> Business:

    if business.status != BusinessStatus.SUSPENDED:
        raise ValueError("Only suspended businesses can be reactivated")

    business.status = BusinessStatus.APPROVED
    business.suspension_reason = None
    business.suspended_at = None

    return _commit(db, business)

# INTERNAL COMMIT HELPER


def _commit(db: Session, instance):
    try:
        db.add(instance)
        db.commit()
        db.refresh(instance)
        return instance
    except SQLAlchemyError as e:
        db.rollback()
        raise Exception(f"Database Error: {str(e)}")
    
def get_all_listings_repo(
    db: Session
):

    listings = db.query(PublicListing).order_by(
        PublicListing.created_at.desc()
    ).all()

    total = len(listings)

    return total, listings

# GET LISTING BY ID
def get_listing_by_id_repo(
    db: Session,
    listingId
):

    return db.query(PublicListing).filter(
        PublicListing.id == listingId
    ).first()

# APPROVE LISTING
def approve_listing_repo(
    db: Session,
    listing
):
    listing.status = "approved"
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
    listing.rejectionReason = reason
    db.commit()
    db.refresh(listing)
    return listing

def suspend_listing_repo(
    db,
    listing,
    reason
):
    listing.status = "suspended"
    listing.suspendedAt = datetime.utcnow()
    listing.suspensionReason = reason
    db.commit()
    db.refresh(listing)
    return listing

def reactivate_listing_repo(
    db,
    listing
):
    listing.status = "approved"
    listing.suspendedAt = None
    listing.suspensionReason = None
    db.commit()
    db.refresh(listing)
    return listing

# GET CATEGORY BY ID
def get_category_by_id_repo(
    db: Session,
    categoryId
):

    return db.query(Category).filter(
        Category.id == categoryId,
        Category.isDeleted == False
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
        isActive=payload.isActive
    )

    db.add(category)
    db.commit()
    db.refresh(category)

    return category