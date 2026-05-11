from sqlalchemy import Column, String, Boolean, DateTime, Float, Text, Integer, ARRAY, ForeignKey
from datetime import datetime
from app.db.database import Base
import uuid
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

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

    createdAt = Column(DateTime, default=datetime.utcnow)

class PublicListing(Base):

    __tablename__ = "merchant_listings"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    businessId = Column(UUID(as_uuid=True))

    listingType = Column(String)
    title = Column(String)
    description = Column(Text)
    categoryId = Column(UUID(as_uuid=True))
    subcategoryId = Column(UUID(as_uuid=True))
    price = Column(Float)
    currency = Column(String)
    images = Column(ARRAY(String))
    status = Column(String)
    tags = Column(ARRAY(String))
    duration = Column(String)
    serviceMode = Column(String)
    availability = Column(String)
    schedule = Column(String)
    startDate = Column(DateTime)
    endDate = Column(DateTime)
    capacity = Column(Integer)
    location = Column(String)
    isOnline = Column(Boolean)
    registrationDeadline = Column(DateTime)
    category = relationship(
        "Category",
        foreign_keys=[categoryId]
    )
    subcategory = relationship(
        "Category",
        foreign_keys=[subcategoryId]
    )

class Category(Base):

    __tablename__ = "categories"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    name = Column(
        String,
        nullable=False,
        unique=True
    )
    description = Column(
        String,
        nullable=True
    )
    image = Column(
        String,
        nullable=True
    )
    status = Column(
        String,
        default="active"
    )
    isDeleted = Column(
        Boolean,
        default=False
    )
    createdAt = Column(
        DateTime,
        default=datetime.utcnow
    )