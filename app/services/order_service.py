import uuid
from datetime import datetime
from math import ceil

from fastapi import status
from sqlalchemy.orm import Session

from app.exceptions.custom_exception import CustomException
from app.models.order_model import Order, OrderItem, OrderStatusHistory
from app.repository.order_repo import (
    create_order,
    create_order_items,
    create_order_status_history,
    get_active_listing_for_business,
    get_business_by_id,
    get_order_by_id,
    list_customer_orders,
    list_merchant_orders,
    update_order,
)


VALID_ORDER_STATUSES = {"Pending", "Confirmed", "Processing", "Completed", "Cancelled", "Refunded"}


def _parse_uuid(value: str, field_name: str):
    try:
        return uuid.UUID(str(value))
    except Exception:
        raise CustomException(400, f"Invalid {field_name}")


def _order_number() -> str:
    return f"ORD-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[:8].upper()}"


def _order_to_dict(order: Order):
    return {
        "order_id": str(order.id),
        "order_number": order.order_number,
        "customer_id": str(order.customer_id),
        "merchant_id": str(order.merchant_id) if order.merchant_id else None,
        "business_id": str(order.business_id),
        "total_amount": float(order.total_amount),
        "tax_amount": float(order.tax_amount),
        "shipping_amount": float(order.shipping_amount),
        "discount_amount": float(order.discount_amount),
        "final_amount": float(order.final_amount),
        "payment_status": order.payment_status,
        "order_status": order.order_status,
        "payment_method": order.payment_method,
        "notes": order.notes,
        "created_at": order.created_at,
        "items": [
            {
                "id": str(item.id),
                "listing_id": str(item.listing_id),
                "listing_name": item.listing_name,
                "listing_type": item.listing_type,
                "quantity": item.quantity,
                "unit_price": float(item.unit_price),
                "total_price": float(item.total_price),
            }
            for item in order.items
        ],
    }


def _page_meta(total: int, page: int, size: int):
    return {
        "page": page,
        "size": size,
        "total_elements": total,
        "total_pages": ceil(total / size) if size else 0,
    }


def create_order_service(db: Session, customer_id: str, payload):
    try:
        customer_uuid = _parse_uuid(customer_id, "customer_id")
        business_uuid = _parse_uuid(payload.business_id, "business_id")

        business = get_business_by_id(db, business_uuid)
        if not business:
            raise CustomException(404, "Business not found")
        if business.status != "approved" or business.is_deleted:
            raise CustomException(400, "Business is not active")

        line_items = []
        subtotal = 0.0
        for i in payload.items:
            listing_uuid = _parse_uuid(i.listing_id, "listing_id")
            listing = get_active_listing_for_business(db, listing_uuid, business_uuid)
            if not listing:
                raise CustomException(400, f"Listing '{i.listing_id}' is not active or not found")
            if i.quantity < 1:
                raise CustomException(400, "quantity must be at least 1")

            unit_price = float(listing.price or 0)
            total_price = unit_price * i.quantity
            subtotal += total_price
            line_items.append((listing, i.quantity, unit_price, total_price))

        tax_amount = round(subtotal * 0.0, 2)
        shipping_amount = 0.0
        discount_amount = 0.0
        final_amount = round(subtotal + tax_amount + shipping_amount - discount_amount, 2)

        order = Order(
            order_number=_order_number(),
            customer_id=customer_uuid,
            merchant_id=business.merchant_id,
            business_id=business_uuid,
            total_amount=subtotal,
            tax_amount=tax_amount,
            shipping_amount=shipping_amount,
            discount_amount=discount_amount,
            final_amount=final_amount,
            order_status="Pending",
            payment_status="Pending",
            payment_method=payload.payment_method,
            notes=payload.notes,
        )
        order = create_order(db, order)

        items = []
        for listing, qty, unit_price, total_price in line_items:
            items.append(
                OrderItem(
                    order_id=order.id,
                    listing_id=listing.id,
                    listing_name=listing.title,
                    listing_type=listing.listingType,
                    quantity=qty,
                    unit_price=unit_price,
                    total_price=total_price,
                )
            )
        create_order_items(db, items)

        create_order_status_history(
            db,
            OrderStatusHistory(
                order_id=order.id,
                old_status="Pending",
                new_status="Pending",
                updated_by=None,
                remarks="Order created",
            ),
        )

        order = get_order_by_id(db, order.id)
        return {"success": True, "message": "Order created successfully", "data": _order_to_dict(order)}
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def get_order_details_service(db: Session, order_id: str):
    try:
        order_uuid = _parse_uuid(order_id, "order_id")
        order = get_order_by_id(db, order_uuid)
        if not order:
            raise CustomException(404, "Order not found")
        return {"success": True, "data": _order_to_dict(order)}
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def get_customer_orders_service(db: Session, customer_id: str, page: int, size: int):
    try:
        customer_uuid = _parse_uuid(customer_id, "customer_id")
        skip = (page - 1) * size
        total, rows = list_customer_orders(db, customer_uuid, skip, size)
        return {"success": True, **_page_meta(total, page, size), "data": [_order_to_dict(o) for o in rows]}
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def get_merchant_orders_service(db: Session, merchant_id: str, page: int, size: int):
    try:
        merchant_uuid = _parse_uuid(merchant_id, "merchant_id")
        skip = (page - 1) * size
        total, rows = list_merchant_orders(db, merchant_uuid, skip, size)
        return {"success": True, **_page_meta(total, page, size), "data": [_order_to_dict(o) for o in rows]}
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def update_order_status_service(db: Session, order_id: str, merchant_id: str, payload):
    try:
        order_uuid = _parse_uuid(order_id, "order_id")
        merchant_uuid = _parse_uuid(merchant_id, "merchant_id")
        order = get_order_by_id(db, order_uuid)
        if not order:
            raise CustomException(404, "Order not found")

        if str(order.merchant_id) != str(merchant_uuid):
            raise CustomException(403, "Merchant can manage only own orders")

        if order.order_status == "Cancelled":
            raise CustomException(400, "Cancelled orders cannot be modified")

        new_status = str(payload.order_status.value if hasattr(payload.order_status, "value") else payload.order_status)
        if new_status not in VALID_ORDER_STATUSES:
            raise CustomException(400, "Invalid order status")

        old_status = order.order_status
        order.order_status = new_status
        update_order(db, order)

        create_order_status_history(
            db,
            OrderStatusHistory(
                order_id=order.id,
                old_status=old_status,
                new_status=new_status,
                updated_by=merchant_uuid,
                remarks=payload.remarks,
            ),
        )
        order = get_order_by_id(db, order.id)
        return {"success": True, "message": "Order status updated", "data": _order_to_dict(order)}
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

