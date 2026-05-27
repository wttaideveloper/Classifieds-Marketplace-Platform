from sqlalchemy.orm import Session
from app.models.capacity_model import (
    ListingCapacity,
    CapacityHistory
)

def get_capacity_by_listing_id(
    db: Session,
    listing_id
):
    return db.query(ListingCapacity).filter(
        ListingCapacity.listing_id == listing_id
    ).first()

def create_capacity(
    db: Session,
    capacity
):
    try:
        db.add(capacity)
        db.commit()
        db.refresh(capacity)
        return capacity
    except Exception:
        db.rollback()
        raise

def update_capacity(
    db: Session,
    capacity
):
    try:
        db.commit()
        db.refresh(capacity)
        return capacity
    except Exception:
        db.rollback()
        raise

def create_capacity_history(
    db: Session,
    history
):
    try:
        db.add(history)
        db.commit()
        db.refresh(history)
        return history
    except Exception:
        db.rollback()
        raise

def get_capacity_history_by_listing_id(
    db: Session,
    listing_id
):
    return db.query(CapacityHistory).filter(
        CapacityHistory.listing_id == listing_id
    ).all()