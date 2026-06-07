from sqlalchemy import Column, String, Boolean, DateTime, Text, Integer, Enum, Numeric, Date, Time, ForeignKey
from datetime import datetime
from app.db.database import Base
import uuid
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import enum
from enum import Enum as PyEnum

class ListingType(str, PyEnum):
    PRODUCT = "product"
    SERVICE = "service"
    EVENT = "event"
    TRAINING = "training"
    PROGRAM = "program"

class BookingStatus(str, PyEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class PaymentStatus(str, PyEnum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"

class Customer(Base):
    __tablename__ = "customers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
<<<<<<< HEAD
    first_name = Column(String)
    last_name = Column(String)
    email = Column(String, unique=True, index=True)
    mobile_number = Column(String)
    password = Column(String)
    accept_terms = Column(Boolean)
    accept_privacy_policy = Column(Boolean)

    reset_token = Column(String, nullable=True)
    reset_token_expiry = Column(DateTime, nullable=True)

    is_email_verified = Column(Boolean, default=False)
    verification_token = Column(String, nullable=True)
=======
    firstName = Column("first_name", String)
    lastName = Column("last_name", String)
    email = Column(String, unique=True, index=True)
    mobileNumber = Column("mobile_number", String)
    password = Column(String)
    acceptTerms = Column("accept_terms", Boolean)
    acceptPrivacyPolicy = Column("accept_privacy_policy", Boolean)

    resetToken = Column("reset_token", String, nullable=True)
    resetTokenExpiry = Column("reset_token_expiry", DateTime, nullable=True)

    isEmailVerified = Column("is_email_verified", Boolean, default=False)
    verificationToken = Column("verification_token", String, nullable=True)
>>>>>>> 04dd8a47f2053fa5dc20b999a276ab54d16b01db
    status = Column(String, default="active")

    created_at = Column(DateTime, default=datetime.utcnow)
    bookings = relationship(
        "Booking",
        back_populates="customer"
    )
   
    wishlists = relationship(
        "Wishlist",
        back_populates="customer",
        cascade="all, delete"
    )

class Booking(Base):
    __tablename__ = "bookings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    booking_number = Column(String, unique=True, nullable=False, index=True)
    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("customers.id"),
        nullable=True
    )
    merchant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id"),
        nullable=True
    )
    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id"),
        nullable=True
    )
    listing_id = Column(
        UUID(as_uuid=True),
        ForeignKey("merchant_listings.id"),
        nullable=False
    )
    listing_type = Column(Enum(ListingType), nullable=False)
    booking_date = Column(Date, nullable=False)
    booking_time = Column(Time, nullable=False)
    quantity = Column(Integer, nullable=False)
    total_amount = Column(Numeric(10, 2), nullable=False)
    booking_status = Column(
        Enum(BookingStatus),
        default=BookingStatus.PENDING,
        nullable=False
    )
    payment_status = Column(
        Enum(PaymentStatus),
        default=PaymentStatus.PENDING,
        nullable=False
    )
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )
    # RELATIONSHIPS
    customer = relationship(
        "Customer",
        back_populates="bookings"
    )
    merchant = relationship(
        "Merchant",
        back_populates="bookings"
    )
    business = relationship(
        "Business",
        back_populates="bookings"
    )
    listing = relationship(
        "MerchantListing",
        back_populates="bookings"
    )
    status_history = relationship(
        "BookingStatusHistory",
        back_populates="booking",
        cascade="all, delete-orphan"
    )
