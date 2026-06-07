import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import status
from sqlalchemy.orm import Session

from app.exceptions.custom_exception import CustomException
from app.models.cart_model import Cart, SavedCart
from app.repository.cart_repo import (
    clear_cart_items,
    delete_cart_item,
    get_cart_item,
    get_cart_item_by_listing,
    get_listing_by_id,
    list_cart_items,
    save_cart_item,
    save_customer_cart,
    update_cart_item,
)


def _parse_uuid(value: str, field_name: str):
    try:
        return uuid.UUID(str(value))
    except Exception:
        raise CustomException(400, f"Invalid {field_name}")


def _listing_is_available(listing, quantity: int) -> bool:
    if getattr(listing, "status", None) != "published":
        return False
    stock_quantity = getattr(listing, "stockQuantity", None)
    if stock_quantity is not None and stock_quantity < quantity:
        return False
    capacity = getattr(listing, "capacity", None)
    if capacity is not None and capacity < quantity:
        return False
    return True


def _cart_item_to_dict(cart_item: Cart):
    return {
        "cart_item_id": cart_item.id,
        "listing_id": cart_item.listing_id,
        "quantity": cart_item.quantity,
        "total_price": cart_item.total_price,
        "merchant_id": cart_item.merchant_id,
    }


def _cart_summary(customer_id, items):
    item_data = [_cart_item_to_dict(item) for item in items]
    grouped = {}
    for item in item_data:
        merchant_id = item["merchant_id"]
        grouped.setdefault(merchant_id, {"items": [], "merchant_total": Decimal("0.00")})
        grouped[merchant_id]["items"].append(item)
        grouped[merchant_id]["merchant_total"] += Decimal(str(item["total_price"]))

    merchants = [
        {
            "merchant_id": merchant_id,
            "items": data["items"],
            "merchant_total": data["merchant_total"],
        }
        for merchant_id, data in grouped.items()
    ]
    return {
        "success": True,
        "customer_id": customer_id,
        "items": item_data,
        "merchants": merchants,
        "total_items": sum(item["quantity"] for item in item_data),
        "cart_total": sum((Decimal(str(item["total_price"])) for item in item_data), Decimal("0.00")),
    }


def add_to_cart_service(db: Session, customer_id: str, payload):
    try:
        customer_uuid = _parse_uuid(customer_id, "customer_id")
        listing_uuid = _parse_uuid(payload.listing_id, "listing_id")
        listing = get_listing_by_id(db, listing_uuid)
        if not listing:
            raise CustomException(404, "Listing not found")
        if not _listing_is_available(listing, payload.quantity):
            raise CustomException(400, "Listing is not available for selected quantity")

        unit_price = Decimal(str(listing.price or 0))
        existing = get_cart_item_by_listing(db, customer_uuid, listing_uuid)
        if existing:
            new_quantity = existing.quantity + payload.quantity
            if not _listing_is_available(listing, new_quantity):
                raise CustomException(400, "Listing is not available for selected quantity")
            existing.quantity = new_quantity
            existing.unit_price = unit_price
            existing.total_price = unit_price * new_quantity
            cart_item = update_cart_item(db, existing)
            message = "Cart item quantity updated"
        else:
            cart_item = Cart(
                customer_id=customer_uuid,
                merchant_id=listing.businessId,
                listing_id=listing_uuid,
                quantity=payload.quantity,
                unit_price=unit_price,
                total_price=unit_price * payload.quantity,
            )
            cart_item = save_cart_item(db, cart_item)
            message = "Item added to cart"

        return {"success": True, "message": message, "data": _cart_item_to_dict(cart_item)}
    except CustomException:
        raise
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def get_cart_service(db: Session, customer_id: str):
    customer_uuid = _parse_uuid(customer_id, "customer_id")
    return _cart_summary(customer_uuid, list_cart_items(db, customer_uuid))


def update_cart_item_service(db: Session, customer_id: str, item_id: str, payload):
    customer_uuid = _parse_uuid(customer_id, "customer_id")
    item_uuid = _parse_uuid(item_id, "item_id")
    cart_item = get_cart_item(db, item_uuid, customer_uuid)
    if not cart_item:
        raise CustomException(404, "Cart item not found")
    listing = get_listing_by_id(db, cart_item.listing_id)
    if not listing or not _listing_is_available(listing, payload.quantity):
        raise CustomException(400, "Listing is not available for selected quantity")
    cart_item.quantity = payload.quantity
    cart_item.total_price = Decimal(str(cart_item.unit_price)) * payload.quantity
    cart_item = update_cart_item(db, cart_item)
    return {"success": True, "message": "Cart item updated", "data": _cart_item_to_dict(cart_item)}


def remove_cart_item_service(db: Session, customer_id: str, item_id: str):
    customer_uuid = _parse_uuid(customer_id, "customer_id")
    item_uuid = _parse_uuid(item_id, "item_id")
    cart_item = get_cart_item(db, item_uuid, customer_uuid)
    if not cart_item:
        raise CustomException(404, "Cart item not found")
    delete_cart_item(db, cart_item)
    return {"success": True, "message": "Cart item removed"}


def clear_cart_service(db: Session, customer_id: str):
    customer_uuid = _parse_uuid(customer_id, "customer_id")
    clear_cart_items(db, customer_uuid)
    return {"success": True, "message": "Cart cleared"}


def checkout_cart_service(db: Session, customer_id: str):
    customer_uuid = _parse_uuid(customer_id, "customer_id")
    items = list_cart_items(db, customer_uuid)
    if not items:
        raise CustomException(400, "Cart is empty")
    for item in items:
        listing = get_listing_by_id(db, item.listing_id)
        if not listing or not _listing_is_available(listing, item.quantity):
            raise CustomException(400, "Cart contains unavailable items")
    summary = _cart_summary(customer_uuid, items)
    return {
        "success": True,
        "message": "Cart validated for checkout",
        "customer_id": summary["customer_id"],
        "merchants": summary["merchants"],
        "total_items": summary["total_items"],
        "cart_total": summary["cart_total"],
        "validated_at": datetime.utcnow(),
    }


def save_cart_service(db: Session, customer_id: str):
    customer_uuid = _parse_uuid(customer_id, "customer_id")
    items = list_cart_items(db, customer_uuid)
    saved_cart = save_customer_cart(
        db,
        SavedCart(customer_id=customer_uuid, cart_data=[_cart_item_to_dict(item) for item in items]),
    )
    return {
        "success": True,
        "message": "Cart saved",
        "customer_id": saved_cart.customer_id,
        "saved_items": len(items),
    }
