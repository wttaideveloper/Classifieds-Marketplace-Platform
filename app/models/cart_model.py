import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.database import Base


class Cart(Base):
    __tablename__ = "carts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    status = Column(String(20), default="active", nullable=False, index=True)
    is_deleted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    items = relationship("CartItem", back_populates="cart", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_carts_user_status", "user_id", "status"),
        Index("ix_carts_tenant_user", "tenant_id", "user_id"),
    )


class CartItem(Base):
    __tablename__ = "cart_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cart_id = Column(UUID(as_uuid=True), ForeignKey("carts.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)
    currency = Column(String(3), default="USD", nullable=False)
    added_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    cart = relationship("Cart", back_populates="items")
    product = relationship("Product")

    __table_args__ = (
        Index("ix_cart_items_cart_product", "cart_id", "product_id", unique=True),
    )


class Order(Base):
    __tablename__ = "orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    status = Column(String(32), default="pending", nullable=False, index=True)       # "cancellation_requested" is 22 characters
    total = Column(Float, nullable=False, default=0)
    currency = Column(String(3), default="USD", nullable=False)
    # Shipping address snapshot (structured object)
    shipping_full_name = Column(String(120), nullable=True)
    shipping_phone = Column(String(20), nullable=True)
    shipping_line1 = Column(String(255), nullable=True)
    shipping_line2 = Column(String(255), nullable=True)
    shipping_city = Column(String(100), nullable=True)
    shipping_state = Column(String(100), nullable=True)
    shipping_zip = Column(String(20), nullable=True)
    shipping_country = Column(String(100), nullable=True)
    refund_reason = Column(Text, nullable=True)
    # --- fulfilment and cancellation (docs/order-cancellation-and-refunds.md) ---
    carrier = Column(String(80), nullable=True)
    tracking_number = Column(String(120), nullable=True)
    tracking_url = Column(Text, nullable=True)
    shipped_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    cancel_reason = Column(Text, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    cancelled_by = Column(UUID(as_uuid=True), nullable=True)
    cancelled_by_role = Column(String(20), nullable=True)
    cancellation_requested_at = Column(DateTime, nullable=True)
    status_before_request = Column(String(32), nullable=True)     # restored when a cancellation request is rejected
    decision_reason = Column(Text, nullable=True)
    stock_reduced = Column(Boolean, default=False, nullable=False, server_default="false")     # checkout took the items out of stock (orders before this existed did not)
    stock_restocked = Column(Boolean, default=False, nullable=False, server_default="false")   # ... and gave them back (only ever once)
    is_deleted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    history = relationship("OrderStatusEvent", back_populates="order", order_by="OrderStatusEvent.created_at", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_orders_user_created", "user_id", "created_at"),
        Index("ix_orders_tenant_status", "tenant_id", "status"),
    )

    @property
    def shipping_address(self):
        if not self.shipping_full_name:
            return None
        return {
            "full_name": self.shipping_full_name,
            "phone": self.shipping_phone,
            "line1": self.shipping_line1,
            "line2": self.shipping_line2,
            "city": self.shipping_city,
            "state": self.shipping_state,
            "zip": self.shipping_zip,
            "country": self.shipping_country,
        }


class OrderStatusEvent(Base):
    """One line of an order's history: who did what, when, and why."""

    __tablename__ = "order_status_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    action = Column(String(40), nullable=False)
    from_status = Column(String(32), nullable=True)
    to_status = Column(String(32), nullable=True)
    actor_id = Column(UUID(as_uuid=True), nullable=True)
    actor_role = Column(String(20), nullable=True)             # customer | admin | super_admin | system
    reason = Column(Text, nullable=True)
    detail = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    order = relationship("Order", back_populates="history")


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    stock_debited = Column(Integer, nullable=False, default=0, server_default="0")
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True)
    product_name = Column(String(255), nullable=False)
    sku = Column(String(100), nullable=True)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)
    line_total = Column(Float, nullable=False)
    currency = Column(String(3), default="USD", nullable=False)

    order = relationship("Order", back_populates="items")
    product = relationship("Product")
