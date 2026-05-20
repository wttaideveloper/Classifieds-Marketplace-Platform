from datetime import datetime
import uuid
from sqlalchemy import Column, String, Numeric, DateTime, Text, Integer, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from app.db.database import Base


class Order(Base):
    __tablename__ = "orders"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_number = Column(String(64), unique=True, nullable=False, index=True)
    customer_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    merchant_id = Column(PG_UUID(as_uuid=True), nullable=True, index=True)
    business_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    total_amount = Column(Numeric(12, 2), nullable=False)
    tax_amount = Column(Numeric(12, 2), nullable=False, default=0)
    shipping_amount = Column(Numeric(12, 2), nullable=False, default=0)
    discount_amount = Column(Numeric(12, 2), nullable=False, default=0)
    final_amount = Column(Numeric(12, 2), nullable=False)
    order_status = Column(String(20), nullable=False, default="Pending")
    payment_status = Column(String(20), nullable=False, default="Pending")
    payment_method = Column(String(50), nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    status_history = relationship("OrderStatusHistory", back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(PG_UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    listing_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    listing_name = Column(String(255), nullable=True)
    listing_type = Column(String(50), nullable=True)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(12, 2), nullable=False)
    total_price = Column(Numeric(12, 2), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    order = relationship("Order", back_populates="items")


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(PG_UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    old_status = Column(String(50), nullable=False)
    new_status = Column(String(50), nullable=False)
    updated_by = Column(PG_UUID(as_uuid=True), nullable=True)
    remarks = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    order = relationship("Order", back_populates="status_history")
