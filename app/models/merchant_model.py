# merchant_model.py
from sqlalchemy import (
    Column,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    JSON,
    Float,
    ARRAY,
    Integer
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime
from app.db.database import Base

# MERCHANT
class Merchant(Base):

    __tablename__ = "merchants"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    fullName = Column(String(255), nullable=False)

    businessEmail = Column(
        String(255),
        unique=True,
        index=True,
        nullable=False
    )

    mobileNumber = Column(String(20), nullable=False)

    businessName = Column(String(255), nullable=False)

    password = Column(String(255), nullable=False)

    acceptTerms = Column(Boolean, default=False, nullable=False)

    acceptPrivacyPolicy = Column(
        Boolean,
        default=False,
        nullable=False
    )

    resetToken = Column(String(255), nullable=True)

    resetTokenExpiry = Column(DateTime, nullable=True)

    isEmailVerified = Column(
        Boolean,
        default=False,
        nullable=False
    )

    verificationToken = Column(String(255), nullable=True)

    status = Column(
        String(50),
        default="active",
        nullable=False
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    business_profile = relationship(
        "MerchantProfile",
        back_populates="merchant",
        uselist=False,
        cascade="all, delete-orphan"
    )

    business_draft = relationship(
        "MerchantBusinessDraft",
        back_populates="merchant",
        uselist=False,
        cascade="all, delete-orphan"
    )
    businesses = relationship(
        "Business",
        back_populates="merchant",
        cascade="all, delete-orphan"
    )

# MERCHANT PROFILE
class MerchantProfile(Base):

    __tablename__ = "merchant_profiles"

    id = Column(String, primary_key=True, index=True)

    merchant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True
    )

    businessName = Column(String(255), nullable=False)

    businessDescription = Column(Text, nullable=True)

    primaryCategory = Column(String(255), nullable=False)

    subcategory = Column(String(255), nullable=True)

    businessEmail = Column(String(255), nullable=False)

    phoneNumber = Column(String(20), nullable=False)

    fullAddress = Column(Text, nullable=False)

    city = Column(String(100), nullable=False)

    state = Column(String(100), nullable=False)

    zipCode = Column(String(20), nullable=False)

    country = Column(String(100), nullable=False)

    latitude = Column(String(50), nullable=True)

    longitude = Column(String(50), nullable=True)

    businessLogo = Column(String(500), nullable=True)

    bannerImage = Column(String(500), nullable=True)

    galleryImages = Column(JSON, default=list)

    operatingHours = Column(JSON, default=dict)

    businessType = Column(String(50), nullable=False)

    cancellationPolicy = Column(Text, nullable=True)

    refundPolicy = Column(Text, nullable=True)

    merchantTermsOfService = Column(Text, nullable=True)

    websiteUrl = Column(String(500), nullable=True)

    socialMediaLinks = Column(JSON, default=dict)

    additionalContactNumbers = Column(JSON, default=list)

    shortTagline = Column(String(255), nullable=True)

    status = Column(String, default="draft")

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    merchant = relationship(
        "Merchant",
        back_populates="business_profile"
    )

# MERCHANT BUSINESS DRAFT
class MerchantBusinessDraft(Base):

    __tablename__ = "merchant_business_drafts"

    id = Column(String, primary_key=True, index=True)

    merchant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True
    )

    businessName = Column(String(255), nullable=True)

    businessDescription = Column(Text, nullable=True)

    primaryCategory = Column(String(255), nullable=True)

    subcategory = Column(String(255), nullable=True)

    businessEmail = Column(String(255), nullable=True)

    phoneNumber = Column(String(20), nullable=True)

    fullAddress = Column(Text, nullable=True)

    city = Column(String(100), nullable=True)

    state = Column(String(100), nullable=True)

    zipCode = Column(String(20), nullable=True)

    country = Column(String(100), nullable=True)

    latitude = Column(String(50), nullable=True)

    longitude = Column(String(50), nullable=True)

    businessLogo = Column(String(500), nullable=True)

    bannerImage = Column(String(500), nullable=True)

    galleryImages = Column(JSON, default=list)

    operatingHours = Column(JSON, default=dict)

    businessType = Column(String(50), nullable=True)

    cancellationPolicy = Column(Text, nullable=True)

    refundPolicy = Column(Text, nullable=True)

    merchantTermsOfService = Column(Text, nullable=True)

    websiteUrl = Column(String(500), nullable=True)

    socialMediaLinks = Column(JSON, default=dict)

    additionalContactNumbers = Column(JSON, default=list)

    shortTagline = Column(String(255), nullable=True)

    status = Column(
        String(50),
        default="draft",
        nullable=False
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    merchant = relationship(
        "Merchant",
        back_populates="business_draft"
    )

# MERCHANT LISTING
class MerchantListing(Base):

    __tablename__ = "merchant_listings"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    businessId = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id"),
        nullable=False
    )

    listingType = Column(
        String,
        nullable=False
    )

    title = Column(
        String,
        nullable=False
    )

    description = Column(
        Text,
        nullable=True
    )

    categoryId = Column(
        UUID(as_uuid=True),
        ForeignKey("categories.id"),
        nullable=True
    )

    subcategoryId = Column(
        UUID(as_uuid=True),
        ForeignKey("categories.id"),
        nullable=True
    )

    price = Column(
        Float,
        nullable=True
    )

    currency = Column(
        String,
        default="INR"
    )

    images = Column(
        ARRAY(String),
        default=list
    )

    status = Column(
        String,
        default="draft"
    )

    tags = Column(
        ARRAY(String),
        default=list
    )

    # PRODUCT
    stockQuantity = Column(Integer, nullable=True)

    sku = Column(String, nullable=True)

    weight = Column(Float, nullable=True)

    # SERVICE
    duration = Column(String, nullable=True)

    serviceMode = Column(String, nullable=True)

    availability = Column(String, nullable=True)

    schedule = Column(String, nullable=True)

    # EVENT / TRAINING / PROGRAM
    startDate = Column(DateTime, nullable=True)

    endDate = Column(DateTime, nullable=True)

    capacity = Column(Integer, nullable=True)

    location = Column(String, nullable=True)

    isOnline = Column(Boolean, default=False)

    registrationDeadline = Column(
        DateTime,
        nullable=True
    )

    rejectionReason = Column(Text, nullable=True)

    rejectedAt = Column(DateTime, nullable=True)

    suspensionReason = Column(Text, nullable=True)

    suspendedAt = Column(DateTime, nullable=True)

    approvedAt = Column(DateTime, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )

    category = relationship(
        "Category",
        foreign_keys=[categoryId]
    )

    subcategory = relationship(
        "Category",
        foreign_keys=[subcategoryId]
    )

# MERCHANT LISTING DRAFT
class MerchantListingDraft(Base):

    __tablename__ = "merchant_listing_drafts"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    businessId = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id"),
        nullable=False
    )

    listingType = Column(
        String,
        nullable=True
    )

    title = Column(
        String,
        nullable=True
    )

    description = Column(
        Text,
        nullable=True
    )

    categoryId = Column(
        UUID(as_uuid=True),
        ForeignKey("categories.id"),
        nullable=True
    )

    subcategoryId = Column(
        UUID(as_uuid=True),
        ForeignKey("categories.id"),
        nullable=True
    )

    price = Column(
        Float,
        nullable=True
    )

    currency = Column(
        String,
        default="INR"
    )

    images = Column(
        ARRAY(String),
        default=list
    )

    tags = Column(
        ARRAY(String),
        default=list
    )

    stockQuantity = Column(Integer, nullable=True)

    sku = Column(String, nullable=True)

    weight = Column(Float, nullable=True)

    duration = Column(String, nullable=True)

    serviceMode = Column(String, nullable=True)

    availability = Column(String, nullable=True)

    schedule = Column(String, nullable=True)

    startDate = Column(DateTime, nullable=True)

    endDate = Column(DateTime, nullable=True)

    capacity = Column(Integer, nullable=True)

    location = Column(String, nullable=True)

    isOnline = Column(Boolean, default=False)

    registrationDeadline = Column(
        DateTime,
        nullable=True
    )

    status = Column(
        String,
        default="draft"
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

    category = relationship(
        "Category",
        foreign_keys=[categoryId]
    )

    subcategory = relationship(
        "Category",
        foreign_keys=[subcategoryId]
    )