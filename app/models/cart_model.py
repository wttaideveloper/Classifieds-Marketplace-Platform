from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, Numeric
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.db.database import Base


class Cart(Base):
    __tablename__ = "cart"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    merchant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    listing_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchant_listings.id"),
        nullable=False,
        index=True,
    )
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(12, 2), nullable=False)
    total_price = Column(Numeric(12, 2), nullable=False)
    added_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class SavedCart(Base):
    __tablename__ = "saved_cart"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    cart_data = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
