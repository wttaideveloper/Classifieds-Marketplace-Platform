# app/models/admin_model.py
from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, ForeignKey, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid
from datetime import datetime
from app.db.database import Base
import enum


class Admin(Base):
    __tablename__ = "admins"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    name = Column(String, nullable=True)  
    email = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)

<<<<<<< HEAD
    reset_token = Column(String, nullable=True)
    reset_token_expiry = Column(DateTime, nullable=True)

    is_email_verified = Column(Boolean, default=False)
    verification_token = Column(String, nullable=True)
=======
    resetToken = Column("reset_token", String, nullable=True)
    resetTokenExpiry = Column("reset_token_expiry", DateTime, nullable=True)

    isEmailVerified = Column("is_email_verified", Boolean, default=False)
    verificationToken = Column("verification_token", String, nullable=True)
>>>>>>> 04dd8a47f2053fa5dc20b999a276ab54d16b01db
    status = Column(String, default="active")

    created_at = Column(DateTime, default=datetime.utcnow)

class Business(Base):
    __tablename__ = "businesses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    name = Column(String, nullable=False)
    category = Column(String, nullable=True)

    status = Column(String, default="pending", index=True)

    is_deleted = Column(Boolean, default=False)

    approved_at = Column(DateTime, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    suspension_reason = Column(Text, nullable=True)
    suspended_at = Column(DateTime, nullable=True)

    merchant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id"),
        nullable=False,
        index=True
    )

    merchant = relationship("Merchant", back_populates="businesses")

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    listings = relationship(
        "MerchantListing",
        back_populates="business"
    )
    bookings = relationship(
        "Booking",
        back_populates="business"
    )
    wishlists = relationship(
        "Wishlist",
        back_populates="business",
        cascade="all, delete"
    )
    media = relationship(
        "Media",
        back_populates="business"
    )

    def __repr__(self):
        return f"<Business {self.name} ({self.status})>"


# FIELD TYPES
class AttributeFieldType(str, enum.Enum):
    TEXT = "text"
    TEXTAREA = "textarea"
    NUMBER = "number"
    DROPDOWN = "dropdown"
    CHECKBOX = "checkbox"
    DATE = "date"


# ATTRIBUTE MASTER TABLE
class Attribute(Base):
    __tablename__ = "attributes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    name = Column(String, nullable=False)

    display_name = Column(String, nullable=False)

    slug = Column(String, unique=True, nullable=False)

    field_type = Column(
        Enum(AttributeFieldType),
        nullable=False
    )

    placeholder = Column(String, nullable=True)

    is_required = Column(Boolean, default=False)

    is_active = Column(Boolean, default=True)

    is_global = Column(Boolean, default=True)

    created_by = Column(UUID(as_uuid=True), nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    options = relationship(
        "AttributeOption",
        back_populates="attribute",
        cascade="all, delete"
    )


# ATTRIBUTE OPTIONS TABLE
class AttributeOption(Base):
    __tablename__ = "attribute_options"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    attribute_id = Column(
        UUID(as_uuid=True),
        ForeignKey("attributes.id", ondelete="CASCADE"),
        nullable=False
    )

    option_label = Column(String, nullable=False)

    option_value = Column(String, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    attribute = relationship(
        "Attribute",
        back_populates="options"
    )

