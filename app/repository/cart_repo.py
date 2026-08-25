from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.models.cart_model import Cart, CartItem, Order, OrderItem


def get_active_cart(db: Session, user_id: UUID) -> Cart | None:
    return (
        db.query(Cart)
        .filter(Cart.user_id == user_id, Cart.status == "active", Cart.is_deleted.is_(False))
        .first()
    )


def get_active_cart_with_items(db: Session, user_id: UUID) -> Cart | None:
    return (
        db.query(Cart)
        .options(joinedload(Cart.items).joinedload(CartItem.product))
        .filter(Cart.user_id == user_id, Cart.status == "active", Cart.is_deleted.is_(False))
        .first()
    )


def get_or_create_cart(db: Session, user_id: UUID, tenant_id: UUID | None = None) -> Cart:
    cart = get_active_cart(db, user_id)
    if cart:
        return cart
    cart = Cart(user_id=user_id, tenant_id=tenant_id, status="active")
    db.add(cart)
    db.flush()
    return cart


def get_cart_item(db: Session, cart_id: UUID, product_id: UUID) -> CartItem | None:
    return (
        db.query(CartItem)
        .filter(CartItem.cart_id == cart_id, CartItem.product_id == product_id)
        .first()
    )


def get_cart_item_by_id(db: Session, user_id: UUID, item_id: UUID) -> tuple[Cart, CartItem] | None:
    item = db.query(CartItem).filter(CartItem.id == item_id).first()
    if not item:
        return None
    cart = (
        db.query(Cart)
        .filter(Cart.id == item.cart_id, Cart.user_id == user_id, Cart.status == "active", Cart.is_deleted.is_(False))
        .first()
    )
    if not cart:
        return None
    return cart, item


def create_order(
    db: Session,
    user_id: UUID,
    tenant_id: UUID | None,
    total: float,
    currency: str,
    shipping_address: dict | None = None,
) -> Order:
    order = Order(
        user_id=user_id,
        tenant_id=tenant_id,
        status="pending",
        total=total,
        currency=currency,
        shipping_full_name=shipping_address.get("full_name") if shipping_address else None,
        shipping_phone=shipping_address.get("phone") if shipping_address else None,
        shipping_line1=shipping_address.get("line1") if shipping_address else None,
        shipping_line2=shipping_address.get("line2") if shipping_address else None,
        shipping_city=shipping_address.get("city") if shipping_address else None,
        shipping_state=shipping_address.get("state") if shipping_address else None,
        shipping_zip=shipping_address.get("zip") if shipping_address else None,
        shipping_country=shipping_address.get("country") if shipping_address else None,
    )
    db.add(order)
    db.flush()
    return order


def add_order_item(
    db: Session,
    order_id: UUID,
    product_id: UUID,
    product_name: str,
    sku: str | None,
    quantity: int,
    unit_price: float,
    currency: str,
) -> OrderItem:
    line_total = round(unit_price * quantity, 2)
    item = OrderItem(
        order_id=order_id,
        product_id=product_id,
        product_name=product_name,
        sku=sku,
        quantity=quantity,
        unit_price=unit_price,
        line_total=line_total,
        currency=currency,
    )
    db.add(item)
    return item


def get_order_by_id(db: Session, order_id: UUID, user_id: UUID | None = None) -> Order | None:
    q = db.query(Order).options(joinedload(Order.items)).filter(Order.id == order_id, Order.is_deleted.is_(False))
    if user_id:
        q = q.filter(Order.user_id == user_id)
    return q.first()


def list_orders(db: Session, user_id: UUID, page: int = 1, page_size: int = 20):
    from app.repository.query_utils import paginate_query

    q = (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(Order.user_id == user_id, Order.is_deleted.is_(False))
        .order_by(Order.created_at.desc())
    )
    return paginate_query(q, page, page_size)
