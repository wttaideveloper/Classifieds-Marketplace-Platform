from sqlalchemy.orm import Session

from app.models.admin_model import Business
from app.models.category_model import Category  # noqa: F401
from app.models.merchant_model import MerchantListing
from app.models.order_model import Order, OrderItem, OrderStatusHistory


def get_business_by_id(db: Session, business_id):
    return db.query(Business).filter(Business.id == business_id).first()


def get_active_listing_for_business(db: Session, listing_id, business_id):
    return (
        db.query(MerchantListing)
        .filter(
            MerchantListing.id == listing_id,
            MerchantListing.businessId == business_id,
            MerchantListing.status == "published",
        )
        .first()
    )


def create_order(db: Session, order: Order):
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def create_order_items(db: Session, items):
    db.add_all(items)
    db.commit()
    for item in items:
        db.refresh(item)
    return items


def create_order_status_history(db: Session, history: OrderStatusHistory):
    db.add(history)
    db.commit()
    db.refresh(history)
    return history


def get_order_by_id(db: Session, order_id):
    return db.query(Order).filter(Order.id == order_id).first()


def list_customer_orders(db: Session, customer_id, skip: int, limit: int):
    query = db.query(Order).filter(Order.customer_id == customer_id)
    total = query.count()
    data = query.order_by(Order.created_at.desc()).offset(skip).limit(limit).all()
    return total, data


def list_merchant_orders(db: Session, merchant_id, skip: int, limit: int):
    query = db.query(Order).filter(Order.merchant_id == merchant_id)
    total = query.count()
    data = query.order_by(Order.created_at.desc()).offset(skip).limit(limit).all()
    return total, data


def update_order(db: Session, order: Order):
    db.commit()
    db.refresh(order)
    return order

