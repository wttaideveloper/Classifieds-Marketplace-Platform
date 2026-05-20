from enum import Enum
import uuid
from sqlalchemy import Column, String, Text, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.database import Base


class ReviewModerationStatus(str, Enum):
    pending = "Pending"
    approved = "Approved"
    rejected = "Rejected"


class Review(Base):
    __tablename__ = "reviews"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    customer_id = Column(
        String,
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    booking_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    listing_id = Column(
        UUID(as_uuid=True),
        ForeignKey("merchant_listings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    rating = Column(Integer, nullable=False)  # 1-5
    review_comment = Column(Text, nullable=True)

    moderation_status = Column(
        String(50),
        default=ReviewModerationStatus.pending.value,
        nullable=False,
        index=True,
    )
    is_verified = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    business = relationship("Business", foreign_keys=[business_id])
    customer = relationship("Customer", foreign_keys=[customer_id])
    listing = relationship("MerchantListing", foreign_keys=[listing_id])
    moderation_history = relationship(
        "ReviewModerationHistory",
        back_populates="review",
        cascade="all, delete-orphan",
    )
