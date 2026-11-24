"""Stock reservations (design D1): catalog's own table in its NEW database (not part of the monolith schema).

A checkout reserves stock here (decremented under row locks, like the monolith's checkout), commerce then
confirms it once the order exists; an unconfirmed reservation is released after its expiry.
"""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, String

from catalog_service.db.database import Base


class StockReservation(Base):
    __tablename__ = "stock_reservations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    idempotency_key = Column(String(255), nullable=False, unique=True)
    items = Column(JSON, nullable=False)          # [{"product_id", "quantity", "unit_price", "currency", "product_name", "sku", "stock_debited"}]
    status = Column(String(20), nullable=False, default="held", index=True)   # held | confirmed | released
    order_ref = Column(String(64), nullable=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
