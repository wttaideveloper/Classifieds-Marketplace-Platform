from sqlalchemy import (
    Column,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    JSON
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime
from app.db.database import Base

# MERCHANT 

class Merchant(Base):
    __tablename__ = "merchants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    fullName = Column(String(255), nullable=False)
    businessEmail = Column(String(255), unique=True, index=True, nullable=False)
    mobileNumber = Column(String(20), nullable=False)
    businessName = Column(String(255), nullable=False)

    password = Column(String(255), nullable=False)

    acceptTerms = Column(Boolean, default=False, nullable=False)
    acceptPrivacyPolicy = Column(Boolean, default=False, nullable=False)

    resetToken = Column(String(255), nullable=True)
    resetTokenExpiry = Column(DateTime, nullable=True)

    isEmailVerified = Column(Boolean, default=False, nullable=False)
    verificationToken = Column(String(255), nullable=True)

    status = Column(String(50), default="active", nullable=False)

    createdAt = Column(DateTime, default=datetime.utcnow)
    updatedAt = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    # One merchant -> one business profile
    business_profile = relationship(
        "MerchantProfile",
        back_populates="merchant",
        uselist=False,
        cascade="all, delete-orphan"
    )

    # One merchant -> one business draft
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

# MERCHANT BUSINESS PROFILE 

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

    # physical | online | hybrid
    businessType = Column(String(50), nullable=False)

    cancellationPolicy = Column(Text, nullable=True)
    refundPolicy = Column(Text, nullable=True)
    merchantTermsOfService = Column(Text, nullable=True)

    websiteUrl = Column(String(500), nullable=True)

    socialMediaLinks = Column(JSON, default=dict)

    additionalContactNumbers = Column(JSON, default=list)

    shortTagline = Column(String(255), nullable=True)
    status = Column(String, default="draft")

    createdAt = Column(DateTime, default=datetime.utcnow)
    updatedAt = Column(
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

    status = Column(String(50), default="draft", nullable=False)

    createdAt = Column(DateTime, default=datetime.utcnow)
    updatedAt = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    merchant = relationship(
        "Merchant",
        back_populates="business_draft"
    )