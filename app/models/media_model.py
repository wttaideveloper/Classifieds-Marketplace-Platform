import uuid
from sqlalchemy import (
    Column,
    String,
    BigInteger,
    Text,
    DateTime,
    ForeignKey
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.db.database import Base


class Media(Base):
    __tablename__ = "media_uploads"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    file_name = Column(
        String(255),
        nullable=False
    )

    original_file_name = Column(
        String(255),
        nullable=False
    )

    file_type = Column(
        String(50),
        nullable=False
    )

    mime_type = Column(
        String(100),
        nullable=False
    )

    file_size = Column(
        BigInteger,
        nullable=False
    )

    file_url = Column(
        Text,
        nullable=False
    )

    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id"),
        nullable=True
    )

    listing_id = Column(
        UUID(as_uuid=True),
        ForeignKey("merchant_listings.id"),
        nullable=True
    )
    business = relationship(
        "Business",
        back_populates="media"
    )
    listing = relationship(
        "MerchantListing",
        back_populates="media"
    )
    
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )