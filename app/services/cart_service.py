from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.cart_model import Cart
from app.models.product_model import Product
from app.repository.cart_repo import (
    add_order_item,
    create_order,
    get_active_cart_with_items,
    get_cart_item,
    get_cart_item_by_id,
    get_or_create_cart,
    list_orders,
)
from app.schemas.cart_schema import (
    CartAddRequest,
    CartResponse,
    CartUpdateRequest,
    CheckoutRequest,
    OrderListItemResponse,
    OrderResponse,
)


def _effective_price(product: Product) -> tuple[float, str]:
    price = product.sale_price if product.sale_price is not None else product.product_price
    currency = product.currency or "USD"
    if price is None:
        price = product.product_price or 0
    return float(price), currency


def _check_stock(product: Product, desired_qty: int):
    if product.is_deleted or product.status == "inactive":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Product is not available")
    if product.stock_quantity is not None and desired_qty > product.stock_quantity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient stock. Available: {product.stock_quantity}",
        )


def _map_cart(cart: Cart | None) -> CartResponse:
    if not cart:
        # caller handles not-found as empty
        return None  # type: ignore
    items = []
    subtotal = 0.0
    currency = "USD"
    for it in (cart.items or []):
        prod = it.product
        prod_name = prod.product_name if prod else None
        prod_images = prod.product_images if prod else None
        sku = prod.sku if prod else None
        stock_qty = prod.stock_quantity if prod else None
        line_total = round(it.unit_price * it.quantity, 2)
        subtotal += line_total
        if it.currency:
            currency = it.currency
        items.append(
            {
                "id": it.id,
                "product_id": it.product_id,
                "product_name": prod_name,
                "product_images": prod_images,
                "sku": sku,
                "quantity": it.quantity,
                "unit_price": it.unit_price,
                "currency": it.currency,
                "line_total": line_total,
                "stock_quantity": stock_qty,
            }
        )
    return CartResponse(
        id=cart.id,
        user_id=cart.user_id,
        tenant_id=cart.tenant_id,
        status=cart.status,
        items=items,  # type: ignore
        subtotal=round(subtotal, 2),
        currency=currency,
        created_at=cart.created_at,
        updated_at=cart.updated_at,
    )


def get_cart_service(db: Session, user_id: UUID) -> CartResponse:
    cart = get_active_cart_with_items(db, user_id)
    if not cart:
        # return empty cart shape with 200 and 0 items? create empty for consistency
        # but to avoid side-effect on GET, return empty virtual cart
        # create if you prefer: cart = get_or_create_cart(db, user_id)
        # here return empty
        return CartResponse(
            id=user_id,  # placeholder id when no cart yet
            user_id=user_id,
            tenant_id=None,
            status="active",
            items=[],
            subtotal=0,
            currency="USD",
            created_at=None,
            updated_at=None,
        )
    return _map_cart(cart)


def add_to_cart_service(db: Session, user_id: UUID, payload: CartAddRequest) -> CartResponse:
    product = db.query(Product).filter(Product.id == payload.product_id).first()
    if not product or product.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    _check_stock(product, payload.quantity)

    unit_price, currency = _effective_price(product)
    tenant_id = product.tenant_id

    cart = get_or_create_cart(db, user_id, tenant_id=tenant_id)
    if cart.tenant_id is None and tenant_id:
        cart.tenant_id = tenant_id

    existing = get_cart_item(db, cart.id, product.id)
    if existing:
        new_qty = existing.quantity + payload.quantity
        if new_qty > 99:
            new_qty = 99
        _check_stock(product, new_qty)
        existing.quantity = new_qty
        existing.unit_price = unit_price
        existing.currency = currency
    else:
        from app.models.cart_model import CartItem

        item = CartItem(
            cart_id=cart.id,
            product_id=product.id,
            quantity=payload.quantity,
            unit_price=unit_price,
            currency=currency,
        )
        db.add(item)

    db.commit()
    db.refresh(cart)
    cart = get_active_cart_with_items(db, user_id)
    return _map_cart(cart)


def update_cart_item_service(db: Session, user_id: UUID, item_id: UUID, payload: CartUpdateRequest) -> CartResponse:
    found = get_cart_item_by_id(db, user_id, item_id)
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart item not found")
    cart, item = found
    product = db.query(Product).filter(Product.id == item.product_id).first()
    if not product or product.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    _check_stock(product, payload.quantity)
    unit_price, currency = _effective_price(product)
    item.quantity = payload.quantity
    item.unit_price = unit_price
    item.currency = currency
    db.commit()
    cart = get_active_cart_with_items(db, user_id)
    return _map_cart(cart)


def remove_cart_item_service(db: Session, user_id: UUID, item_id: UUID) -> CartResponse:
    found = get_cart_item_by_id(db, user_id, item_id)
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart item not found")
    cart, item = found
    db.delete(item)
    db.commit()
    cart = get_active_cart_with_items(db, user_id)
    if not cart or not cart.items:
        return CartResponse(
            id=cart.id if cart else user_id,
            user_id=user_id,
            tenant_id=cart.tenant_id if cart else None,
            status="active",
            items=[],
            subtotal=0,
            currency="USD",
            created_at=cart.created_at if cart else None,
            updated_at=cart.updated_at if cart else None,
        )
    return _map_cart(cart)


def clear_cart_service(db: Session, user_id: UUID) -> dict:
    cart = get_active_cart_with_items(db, user_id)
    if not cart:
        return {"message": "Cart is already empty"}
    for it in list(cart.items):
        db.delete(it)
    db.commit()
    return {"message": "Cart cleared"}


def _order_shipping_address(order) -> dict | None:
    if not order.shipping_full_name:
        return None
    return {
        "full_name": order.shipping_full_name,
        "phone": order.shipping_phone,
        "line1": order.shipping_line1,
        "line2": order.shipping_line2,
        "city": order.shipping_city,
        "state": order.shipping_state,
        "zip": order.shipping_zip,
        "country": order.shipping_country,
    }


def checkout_service(db: Session, user_id: UUID, payload: CheckoutRequest) -> OrderResponse:
    cart = get_active_cart_with_items(db, user_id)
    if not cart or not cart.items:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cart is empty")

    total = 0.0
    currency = "USD"
    # re-validate stock + snapshot
    for it in cart.items:
        product = it.product or db.query(Product).filter(Product.id == it.product_id).first()
        if not product or product.is_deleted:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Product {it.product_id} is not available")
        _check_stock(product, it.quantity)
        unit_price, cur = _effective_price(product)
        # update cart item to latest price
        it.unit_price = unit_price
        it.currency = cur
        currency = cur
        total += unit_price * it.quantity

    total = round(total, 2)
    shipping_dict = payload.shipping_address.model_dump() if payload and payload.shipping_address else None
    order = create_order(db, user_id, cart.tenant_id, total, currency, shipping_address=shipping_dict)
    db.flush()

    for it in cart.items:
        product = it.product or db.query(Product).filter(Product.id == it.product_id).first()
        prod_name = product.product_name if product else str(it.product_id)
        sku = product.sku if product else None
        add_order_item(
            db,
            order.id,
            it.product_id,
            prod_name,
            sku,
            it.quantity,
            it.unit_price,
            it.currency,
        )

    # mark cart converted and empty it
    cart.status = "converted"
    # keep items for history until deleted? delete them to start fresh active cart
    for it in list(cart.items):
        db.delete(it)

    db.commit()
    db.refresh(order)
    # reload with items
    from app.repository.cart_repo import get_order_by_id

    order = get_order_by_id(db, order.id)
    items = [
        {
            "id": oi.id,
            "product_id": oi.product_id,
            "product_name": oi.product_name,
            "sku": oi.sku,
            "quantity": oi.quantity,
            "unit_price": oi.unit_price,
            "line_total": oi.line_total,
            "currency": oi.currency,
        }
        for oi in order.items
    ]
    return OrderResponse(
        id=order.id,
        user_id=order.user_id,
        tenant_id=order.tenant_id,
        status=order.status,
        total=order.total,
        currency=order.currency,
        shipping_address=_order_shipping_address(order),  # type: ignore
        items=items,  # type: ignore
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


def list_orders_service(db: Session, user_id: UUID, page: int = 1, page_size: int = 20):
    from app.repository.query_utils import build_pagination_meta
    from app.schemas.common_schema import PaginatedResponse

    items, total = list_orders(db, user_id, page=page, page_size=page_size)
    mapped = []
    for o in items:
        mapped.append(
            OrderListItemResponse(
                id=o.id,
                user_id=o.user_id,
                tenant_id=o.tenant_id,
                status=o.status,
                total=o.total,
                currency=o.currency,
                item_count=len(o.items) if hasattr(o, "items") else 0,
                created_at=o.created_at,
            )
        )
    # load items count if not joined
    # ensure count correctly when items not loaded: reuse len from DB via query count?
    return PaginatedResponse[OrderListItemResponse](
        items=mapped,
        pagination=build_pagination_meta(total, page, page_size),
    )


def get_order_service(db: Session, user_id: UUID, order_id: UUID) -> OrderResponse:
    from app.repository.cart_repo import get_order_by_id

    order = get_order_by_id(db, order_id, user_id=user_id)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    items = [
        {
            "id": oi.id,
            "product_id": oi.product_id,
            "product_name": oi.product_name,
            "sku": oi.sku,
            "quantity": oi.quantity,
            "unit_price": oi.unit_price,
            "line_total": oi.line_total,
            "currency": oi.currency,
        }
        for oi in order.items
    ]
    return OrderResponse(
        id=order.id,
        user_id=order.user_id,
        tenant_id=order.tenant_id,
        status=order.status,
        total=order.total,
        currency=order.currency,
        shipping_address=_order_shipping_address(order),  # type: ignore
        items=items,  # type: ignore
        created_at=order.created_at,
        updated_at=order.updated_at,
    )
