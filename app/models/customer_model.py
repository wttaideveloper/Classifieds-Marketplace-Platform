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
    product = "product"
    service = "service"
    event = "event"
    training = "training"
    program = "program"

class BookingStatus(str, PyEnum):
    Pending = "Pending"
    Approved = "Approved"
    Rejected = "Rejected"
    Completed = "Completed"
    Cancelled = "Cancelled"

class PaymentStatus(str, PyEnum):
    Pending = "Pending"
    Paid = "Paid"
    Failed = "Failed"
    Refunded = "Refunded"

class Customer(Base):
    __tablename__ = "customers"

    id = Column(String, primary_key=True, index=True)
    firstName = Column(String)
    lastName = Column(String)
    email = Column(String, unique=True, index=True)
    mobileNumber = Column(String)
    password = Column(String)
    acceptTerms = Column(Boolean)
    acceptPrivacyPolicy = Column(Boolean)

    resetToken = Column(String, nullable=True)
    resetTokenExpiry = Column(DateTime, nullable=True)

    isEmailVerified = Column(Boolean, default=False)
    verificationToken = Column(String, nullable=True)
    status = Column(String, default="active")

    created_at = Column(DateTime, default=datetime.utcnow)
    bookings = relationship(
        "Booking",
        back_populates="customer"
    )

class Booking(Base):
    __tablename__ = "bookings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    booking_number = Column(String, unique=True, nullable=False, index=True)
    customer_id = Column(
        String,
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
        default=BookingStatus.Pending,
        nullable=False
    )
    payment_status = Column(
        Enum(PaymentStatus),
        default=PaymentStatus.Pending,
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