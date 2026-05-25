from sqlalchemy.orm import Session
from app.models.wishlist_model import Wishlist

def get_wishlist_by_customer_and_business(
    db: Session,
    customer_id,
    business_id
):
    return db.query(Wishlist).filter(
        Wishlist.customer_id == customer_id,
        Wishlist.business_id == business_id
    ).first()


def get_wishlist_by_customer_and_listing(
    db: Session,
    customer_id,
    listing_id
):
    return db.query(Wishlist).filter(
        Wishlist.customer_id == customer_id,
        Wishlist.listing_id == listing_id
    ).first()

def create_wishlist(
    db: Session,
    wishlist
):
    try:
        db.add(wishlist)
        db.commit()
        db.refresh(wishlist)
        return wishlist

    except Exception:
        db.rollback()
        raise

def get_all_wishlist(
    db: Session
):
    return db.query(Wishlist).all()

def get_wishlist_by_id(
    db: Session,
    wishlist_id
):
    return db.query(Wishlist).filter(
        Wishlist.id == wishlist_id
    ).first()

def delete_wishlist(
    db: Session,
    wishlist
):
    try:
        db.delete(wishlist)
        db.commit()

    except Exception:
        db.rollback()
        raise

def get_business_wishlist(
    db: Session
):
    return db.query(Wishlist).filter(
        Wishlist.business_id.isnot(None)
    ).all()

def get_listing_wishlist(
    db: Session,
    skip: int,
    limit: int
):
    return (
        db.query(Wishlist)
        .filter(Wishlist.listing_id.isnot(None))
        .order_by(Wishlist.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )