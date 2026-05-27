from sqlalchemy import Column, Enum, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
import enum
from enum import Enum as PyEnum
from app.db.database import Base


class WishlistType(str, PyEnum):
    BUSINESS = "business"
    LISTING = "listing"


class Wishlist(Base):
    __tablename__ = "wishlists"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False
    )

    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=True
    )

    listing_id = Column(
        UUID(as_uuid=True),
        ForeignKey("merchant_listings.id", ondelete="CASCADE"),
        nullable=True
    )

    wishlist_type = Column(
        Enum(WishlistType, name="wishlist_type_enum"),
        nullable=False
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )
    # Relationships
    customer = relationship(
        "Customer",
        back_populates="wishlists"
    )

    business = relationship(
        "Business",
        back_populates="wishlists"
    )

    listing = relationship(
        "MerchantListing",
        back_populates="wishlists"
    )