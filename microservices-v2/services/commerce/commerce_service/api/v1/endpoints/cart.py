from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from mskit.auth.dependencies import extract_access_token, get_current_user
from commerce_service.core.payment_access import PayActor, get_pay_actor, require_merchant_or_admin
from commerce_service.db.database import get_db
from commerce_service.schemas.cart_schema import CartAddRequest, OrderDetailResponse, CartResponse, CartUpdateRequest, CheckoutRequest, OrderResponse
from commerce_service.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from commerce_service.services.cart_service import (
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
    summary="List orders: mine, or (Enterprise Admin) the enterprise's",
    description=(
        "A customer gets their own orders (`id, user_id, status, total, currency, item_count, created_at`). The **Enterprise Admin** gets every order of their own tenant, "
        "newest first, each with `payment_status`, `customer` (name and phone from the shipping address), `item_count`, `items_preview`, `carrier`, `tracking_number`, "
        "`cancellation_requested_at` and `city`. Filters (admin): `status` (comma separated), `q` (customer name or phone, tracking number, order id prefix, product name), "
        "`enterprise_id` (orders containing that enterprise's products), `date_from`, `date_to`. `mine=true` gives an admin their own purchases instead. A Super Admin sees all "
        "tenants (`tenant_id` narrows it). Never another tenant's orders."
    ),
)
def list_orders(
    status_filter: str | None = Query(None, alias="status"),
    q: str | None = Query(None, max_length=200),
    enterprise_id: UUID | None = Query(None),
    tenant_id: UUID | None = Query(None, description="Super Admin only"),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    mine: bool = Query(False, description="An admin asking for the orders they placed themselves"),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    actor: PayActor = Depends(get_pay_actor),
):
    if mine or actor.role == "customer":
        return list_orders_service(db, actor.user_uuid, page=page, page_size=page_size)
    return order_service.list_for_staff(
        db, _staff(actor), status=status_filter, q=q, enterprise_id=enterprise_id, tenant_id=tenant_id, date_from=date_from, date_to=date_to, page=page, page_size=page_size,
    )


@router.get(
    "/orders/cancellation-requests",
    summary="Cancellation requests waiting for a decision (Enterprise Admin / Super Admin)",
    description="Orders in `cancellation_requested`, oldest request first, in the list shape above. Decide each with `POST /orders/{id}/cancel/decision`.",
)
def cancellation_requests(
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    actor: PayActor = Depends(require_merchant_or_admin),
):
    return order_service.list_for_staff(db, _staff(actor), only_requests=True, page=page, page_size=page_size)


@router.get(
    "/orders/{order_id}",
    response_model=OrderDetailResponse,
    summary="Get Order By ID (mine, or any order of the Enterprise Admin's tenant)",
    description=(
        "The customer's own order. The Enterprise Admin also gets any order of their tenant, with `customer`, `allowed_statuses` and the full `history`; a Super Admin any order. "
        "An order that is not yours and not your tenant's is a 404."
    ),
)
def get_order(
    order_id: UUID,
    db: Session = Depends(get_db),
    actor: PayActor = Depends(get_pay_actor),
):
    try:
        return get_order_service(db, actor.user_uuid, order_id)
    except HTTPException as exc:
        if exc.status_code != 404 or actor.role == "customer":
            raise
    order = order_service.staff_order(db, _staff(actor), order_id)
    return order_service.order_view(db, order, staff=True)


from commerce_service.schemas.cart_schema import OrderCancelRequest, OrderRefundRequest, OrderStatusUpdate
from commerce_service.schemas.refund_schema import RefundCreate, RefundDecision, RefundResponse
from commerce_service.services import cart_lifecycle, order_service, payment_view, refund_service
from commerce_service.models.payment_model import Refund


@router.post("/orders/{order_id}/cancel", response_model=OrderDetailResponse, summary="Cancel my order before shipment")
def cancel_order(request: Request, order_id: UUID, payload: OrderCancelRequest | None = None, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    user_id = UUID(str(current_user["id"]))
    order = cart_lifecycle.locked_order(db, order_id, user_id=user_id)
    # the customer's own token goes along: the identity lookup of the store's admins (to tell them) needs a bearer token
    order_service.cancel_order(db, user_id, order.id, payload.reason if payload else None, access_token=extract_access_token(request))
    return get_order_service(db, user_id, order_id)


@router.patch("/orders/{order_id}/status", summary="Change fulfillment status (Enterprise Admin / Super Admin)")
def update_order_status(order_id: UUID, payload: OrderStatusUpdate, db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    cart_lifecycle.locked_order(db, order_id, admin=actor)
    order = order_service.change_status(db, _staff(actor), order_id, payload.status, payload.reason, payload.carrier, payload.tracking_number, payload.tracking_url,
                                        access_token=actor.token)
    return {"id": str(order.id), "status": order.status, "message": f"Order status updated to '{order.status}'"}


def _staff(actor):
    from types import SimpleNamespace
    return SimpleNamespace(id=actor.user_uuid, role="super_admin" if actor.is_super_admin else "admin", tenant_id=actor.tenant_id)


@router.post("/orders/{order_id}/cancel/decision", response_model=OrderDetailResponse, summary="Approve or reject a cancellation request (admin only)")
def cancellation_decision(order_id: UUID, payload: RefundDecision, db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    cart_lifecycle.locked_order(db, order_id, admin=actor)
    order = order_service.decide_cancellation(db, _staff(actor), order_id, payload.action == "approve", payload.reason, access_token=actor.token)
    return order_service.order_view(db, order)



@router.post("/orders/{order_id}/refund", deprecated=True, response_model=RefundResponse, summary="Request refund (prefer POST /api/v1/refunds)")
def request_refund(order_id: UUID, payload: OrderRefundRequest, db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    from fastapi import HTTPException
    order = cart_lifecycle.locked_order(db, order_id, user_id=actor.user_uuid)
    payment, _ = order_service.payment_of(db, order)
    if not payment:
        raise HTTPException(409, "No refundable payment")
    refund = refund_service.request_refund(db, actor, RefundCreate(payment_id=payment.id, amount=payload.amount, reason=payload.reason), access_token=actor.token)
    return payment_view.refund_response(db, refund, actor)


@router.post("/orders/{order_id}/refund/approve", response_model=RefundResponse, summary="Decide a pending refund (Enterprise Admin / Super Admin)")
def approve_refund(order_id: UUID, payload: RefundDecision, db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    from fastapi import HTTPException
    order = cart_lifecycle.locked_order(db, order_id, admin=actor)
    payment, _ = order_service.payment_of(db, order)
    pending = db.query(Refund).filter_by(payment_id=payment.id, status="requested").limit(2).all() if payment else []
    if len(pending) != 1:
        raise HTTPException(409, "Expected one pending refund; use the refund decision endpoint with its ID")
    refund = refund_service.decide_refund(db, actor, pending[0].id, payload.action, payload.reason, access_token=actor.token)
    return payment_view.refund_response(db, refund, actor)
