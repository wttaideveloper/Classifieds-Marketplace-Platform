import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.database import Base


class Product(Base):
    __tablename__ = "products"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    enterprise_id = Column(
        UUID(as_uuid=True),
        ForeignKey("enterprises.id"),
        nullable=False,
    )

    product_name = Column(String(255), nullable=False)

    product_description = Column(Text)

    product_category = Column(String(100), nullable=False)

    product_price = Column(Float, nullable=False)

    product_images = Column(Text)

    product_status = Column(Boolean, default=True)

    sku = Column(String(100))

    barcode_upc = Column(String(100))

    weight = Column(Float)

    dimensions = Column(String(255))

    sale_price = Column(Float)

    cost_price = Column(Float)

    tax_class = Column(String(50))

    currency = Column(String(3), default="USD")

    stock_quantity = Column(Integer, default=0)

    low_stock_alert_threshold = Column(Integer)

    stock_management = Column(String(50))

    publish_status = Column(String(50), default="draft")

    length = Column(Float)

    width = Column(Float)

    thick = Column(Float)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    enterprise = relationship("Enterprise", backref="products")
