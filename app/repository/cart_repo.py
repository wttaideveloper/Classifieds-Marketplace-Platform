from sqlalchemy.orm import Session

from app.models.cart_model import Cart, SavedCart
from app.models.merchant_model import MerchantListing


def get_listing_by_id(db: Session, listing_id):
    return db.query(MerchantListing).filter(MerchantListing.id == listing_id).first()


def get_cart_item(db: Session, cart_item_id, customer_id):
    return (
        db.query(Cart)
        .filter(Cart.id == cart_item_id, Cart.customer_id == customer_id)
        .first()
    )


def get_cart_item_by_listing(db: Session, customer_id, listing_id):
    return (
        db.query(Cart)
        .filter(Cart.customer_id == customer_id, Cart.listing_id == listing_id)
        .first()
    )


def list_cart_items(db: Session, customer_id):
    return db.query(Cart).filter(Cart.customer_id == customer_id).all()


def save_cart_item(db: Session, cart_item: Cart):
    db.add(cart_item)
    db.commit()
    db.refresh(cart_item)
    return cart_item


def update_cart_item(db: Session, cart_item: Cart):
    db.commit()
    db.refresh(cart_item)
    return cart_item


def delete_cart_item(db: Session, cart_item: Cart):
    db.delete(cart_item)
    db.commit()


def clear_cart_items(db: Session, customer_id):
    db.query(Cart).filter(Cart.customer_id == customer_id).delete()
    db.commit()


def save_customer_cart(db: Session, saved_cart: SavedCart):
    db.add(saved_cart)
    db.commit()
    db.refresh(saved_cart)
    return saved_cart
