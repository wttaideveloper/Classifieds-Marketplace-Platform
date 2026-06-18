import uuid

from sqlalchemy import (
    Column,
    String,
    Text,
    Float,
    Boolean,
    ForeignKey
)

from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.database import Base


class Product(Base):
    __tablename__ = "products"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    enterprise_id = Column(
        UUID(as_uuid=True),
        ForeignKey("enterprises.id"),
        nullable=False
    )

    product_name = Column(
        String(255),
        nullable=False
    )

    product_description = Column(Text)

    product_category = Column(
        String(100),
        nullable=False
    )

    product_price = Column(
        Float,
        nullable=False
    )

    product_images = Column(Text)

    product_status = Column(
        Boolean,
        default=True
    )

    enterprise = relationship(
        "Enterprise",
        backref="products"
    )