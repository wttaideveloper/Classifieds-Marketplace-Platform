from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.schemas.cart_schema import CartAddRequest, CartResponse, CartUpdateRequest, CheckoutRequest, OrderResponse
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.services.cart_service import (
    add_to_cart_service,
    checkout_service,
    clear_cart_service,
    get_cart_service,
    get_order_service,
    list_orders_service,
    remove_cart_item_service,
    update_cart_item_service,
)

router = APIRouter(tags=["Cart"])


@router.post(
    "/",
    response_model=CartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add to Cart",
)
def add_to_cart(
    payload: CartAddRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = UUID(str(current_user["id"]))
    return add_to_cart_service(db, user_id, payload)


@router.get(
    "/",
    response_model=CartResponse,
    summary="Get My Cart",
)
def get_cart(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = UUID(str(current_user["id"]))
    return get_cart_service(db, user_id)


@router.patch(
    "/items/{item_id}",
    response_model=CartResponse,
    summary="Update Cart Item Quantity",
)
def update_cart_item(
    item_id: UUID,
    payload: CartUpdateRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = UUID(str(current_user["id"]))
    return update_cart_item_service(db, user_id, item_id, payload)


@router.delete(
    "/items/{item_id}",
    response_model=CartResponse,
    summary="Remove Item From Cart",
)
def remove_cart_item(
    item_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = UUID(str(current_user["id"]))
    return remove_cart_item_service(db, user_id, item_id)


@router.delete(
    "/",
    summary="Clear Cart",
)
def clear_cart(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = UUID(str(current_user["id"]))
    return clear_cart_service(db, user_id)


@router.post(
    "/checkout",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Checkout Cart",
)
def checkout(
    payload: CheckoutRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = UUID(str(current_user["id"]))
    return checkout_service(db, user_id, payload)


@router.get(
    "/orders",
    summary="List My Orders",
)
def list_orders(
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = UUID(str(current_user["id"]))
    return list_orders_service(db, user_id, page=page, page_size=page_size)


@router.get(
    "/orders/{order_id}",
    response_model=OrderResponse,
    summary="Get Order By ID",
)
def get_order(
    order_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = UUID(str(current_user["id"]))
    return get_order_service(db, user_id, order_id)


# ---- Order Status & Refund Endpoints ----

from app.schemas.cart_schema import OrderRefundRequest, OrderStatusUpdate
from app.services.cart_service import update_order_status_service, request_order_refund_service, approve_order_refund_service


@router.patch(
    "/orders/{order_id}/status",
    summary="Update Order Status",
)
def update_order_status(
    order_id: UUID,
    payload: OrderStatusUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = UUID(str(current_user["id"]))
    return update_order_status_service(db, user_id, order_id, payload)


@router.post(
    "/orders/{order_id}/refund",
    summary="Request Order Refund",
)
def request_refund(
    order_id: UUID,
    payload: OrderRefundRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = UUID(str(current_user["id"]))
    return request_order_refund_service(db, user_id, order_id, payload)


@router.post(
    "/orders/{order_id}/refund/approve",
    summary="Approve or Reject Refund (Admin/Provider)",
)
def approve_refund(
    order_id: UUID,
    payload: OrderRefundRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = UUID(str(current_user["id"]))
    return approve_order_refund_service(db, user_id, order_id, payload)
