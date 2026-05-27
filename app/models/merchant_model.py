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
    Integer,
    UniqueConstraint
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

    fullName = Column("full_name", String(255), nullable=False)

    businessEmail = Column(
        "business_email",
        String(255),
        unique=True,
        index=True,
        nullable=False
    )

    mobileNumber = Column("mobile_number", String(20), nullable=False)

    businessName = Column("business_name", String(255), nullable=False)

    password = Column(String(255), nullable=False)

    acceptTerms = Column("accept_terms", Boolean, default=False, nullable=False)

    acceptPrivacyPolicy = Column(
        "accept_privacy_policy",
        Boolean,
        default=False,
        nullable=False
    )

    resetToken = Column("reset_token", String(255), nullable=True)

    resetTokenExpiry = Column("reset_token_expiry", DateTime, nullable=True)

    isEmailVerified = Column(
        "is_email_verified",
        Boolean,
        default=False,
        nullable=False
    )

    verificationToken = Column("verification_token", String(255), nullable=True)

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

    # ONE MERCHANT CAN HAVE MULTIPLE BUSINESSES
    businesses = relationship(
        "Business",
        back_populates="merchant",
        cascade="all, delete-orphan"
    )
    bookings = relationship(
        "Booking",
        back_populates="merchant"
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

    businessName = Column("business_name", String(255), nullable=False)

    businessDescription = Column("business_description", Text, nullable=True)

    primaryCategory = Column("primary_category", String(255), nullable=False)

    subcategory = Column(String(255), nullable=True)

    businessEmail = Column("business_email", String(255), nullable=False)

    phoneNumber = Column("phone_number", String(20), nullable=False)

    fullAddress = Column("full_address", Text, nullable=False)

    city = Column(String(100), nullable=False)

    state = Column(String(100), nullable=False)

    zipCode = Column("zip_code", String(20), nullable=False)

    country = Column(String(100), nullable=False)

    latitude = Column(String(50), nullable=True)

    longitude = Column(String(50), nullable=True)

    businessLogo = Column("business_logo", String(500), nullable=True)

    bannerImage = Column("banner_image", String(500), nullable=True)

    galleryImages = Column("gallery_images", JSON, default=list)

    operatingHours = Column("operating_hours", JSON, default=dict)

    businessType = Column("business_type", String(50), nullable=False)

    cancellationPolicy = Column("cancellation_policy", Text, nullable=True)

    refundPolicy = Column("refund_policy", Text, nullable=True)

    merchantTermsOfService = Column("merchant_terms_of_service", Text, nullable=True)

    websiteUrl = Column("website_url", String(500), nullable=True)

    socialMediaLinks = Column("social_media_links", JSON, default=dict)

    additionalContactNumbers = Column("additional_contact_numbers", JSON, default=list)

    shortTagline = Column("short_tagline", String(255), nullable=True)

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

    businessName = Column("business_name", String(255), nullable=True)

    businessDescription = Column("business_description", Text, nullable=True)

    primaryCategory = Column("primary_category", String(255), nullable=True)

    subcategory = Column(String(255), nullable=True)

    businessEmail = Column("business_email", String(255), nullable=True)

    phoneNumber = Column("phone_number", String(20), nullable=True)

    fullAddress = Column("full_address", Text, nullable=True)

    city = Column(String(100), nullable=True)

    state = Column(String(100), nullable=True)

    zipCode = Column("zip_code", String(20), nullable=True)

    country = Column(String(100), nullable=True)

    latitude = Column(String(50), nullable=True)

    longitude = Column(String(50), nullable=True)

    businessLogo = Column("business_logo", String(500), nullable=True)

    bannerImage = Column("banner_image", String(500), nullable=True)

    galleryImages = Column("gallery_images", JSON, default=list)

    operatingHours = Column("operating_hours", JSON, default=dict)

    businessType = Column("business_type", String(50), nullable=True)

    cancellationPolicy = Column("cancellation_policy", Text, nullable=True)

    refundPolicy = Column("refund_policy", Text, nullable=True)

    merchantTermsOfService = Column("merchant_terms_of_service", Text, nullable=True)

    websiteUrl = Column("website_url", String(500), nullable=True)

    socialMediaLinks = Column("social_media_links", JSON, default=dict)

    additionalContactNumbers = Column("additional_contact_numbers", JSON, default=list)

    shortTagline = Column("short_tagline", String(255), nullable=True)

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
        "business_id",
        UUID(as_uuid=True),
        ForeignKey("businesses.id"),
        nullable=False
    )

    listingType = Column(
        "listing_type",
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
        "category_id",
        UUID(as_uuid=True),
        ForeignKey("categories.id"),
        nullable=True
    )

    subcategoryId = Column(
        "subcategory_id",
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
    stockQuantity = Column("stock_quantity", Integer, nullable=True)

    sku = Column(String, nullable=True)

    weight = Column(Float, nullable=True)

    # SERVICE
    duration = Column(String, nullable=True)

    serviceMode = Column("service_mode", String, nullable=True)

    availability = Column(String, nullable=True)

    schedule = Column(String, nullable=True)

    # EVENT / TRAINING / PROGRAM
    startDate = Column("start_date", DateTime, nullable=True)

    endDate = Column("end_date", DateTime, nullable=True)

    capacity = Column(Integer, nullable=True)

    location = Column(String, nullable=True)

    isOnline = Column("is_online", Boolean, default=False)

    registrationDeadline = Column(
        "registration_deadline",
        DateTime,
        nullable=True
    )

    rejectionReason = Column("rejection_reason", Text, nullable=True)

    rejectedAt = Column("rejected_at", DateTime, nullable=True)

    suspensionReason = Column("suspension_reason", Text, nullable=True)

    suspendedAt = Column("suspended_at", DateTime, nullable=True)

    approvedAt = Column("approved_at", DateTime, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )
    business = relationship(
        "Business",
        back_populates="listings"
    )

    category = relationship(
        "Category",
        foreign_keys=[categoryId]
    )

    subcategory = relationship(
        "Category",
        foreign_keys=[subcategoryId]
    )
    bookings = relationship(
        "Booking",
        back_populates="listing"
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
        "business_id",
        UUID(as_uuid=True),
        ForeignKey("businesses.id"),
        nullable=False
    )

    listingType = Column(
        "listing_type",
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
        "category_id",
        UUID(as_uuid=True),
        ForeignKey("categories.id"),
        nullable=True
    )

    subcategoryId = Column(
        "subcategory_id",
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

    stockQuantity = Column("stock_quantity", Integer, nullable=True)

    sku = Column(String, nullable=True)

    weight = Column(Float, nullable=True)

    duration = Column(String, nullable=True)

    serviceMode = Column("service_mode", String, nullable=True)

    availability = Column(String, nullable=True)

    schedule = Column(String, nullable=True)

    startDate = Column("start_date", DateTime, nullable=True)

    endDate = Column("end_date", DateTime, nullable=True)

    capacity = Column(Integer, nullable=True)

    location = Column(String, nullable=True)

    isOnline = Column("is_online", Boolean, default=False)

    registrationDeadline = Column(
        "registration_deadline",
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

# CUSTOM ATTRIBUTE TABLE
class MerchantCustomAttribute(Base):
    __tablename__ = "merchant_custom_attributes"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    merchant_id = Column(
        UUID(as_uuid=True),
        nullable=False
    )

    attribute_id = Column(
        UUID(as_uuid=True),
        ForeignKey("attributes.id", ondelete="CASCADE"),
        nullable=False
    )

    custom_label = Column(
        String,
        nullable=True
    )

    custom_placeholder = Column(
        String,
        nullable=True
    )

    is_required = Column(
        Boolean,
        default=False
    )

    default_value = Column(
        Text,
        nullable=True
    )

    is_active = Column(
        Boolean,
        default=True
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

# BUSINESS ATTRIBUTE MAPPING
class BusinessAttributeMapping(Base):
    __tablename__ = "business_attributes"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    business_id = Column(
        UUID(as_uuid=True),
        nullable=False
    )

    attribute_id = Column(
        UUID(as_uuid=True),
        ForeignKey("attributes.id", ondelete="CASCADE"),
        nullable=False
    )

    attribute_value = Column(
        Text,
        nullable=True
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

    __table_args__ = (
        UniqueConstraint(
            "business_id",
            "attribute_id",
            name="uq_business_attribute"
        ),
    )

# LISTING ATTRIBUTE MAPPING
class ListingAttributeMapping(Base):
    __tablename__ = "listing_attributes"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    listing_id = Column(
        UUID(as_uuid=True),
        nullable=False
    )

    attribute_id = Column(
        UUID(as_uuid=True),
        ForeignKey("attributes.id", ondelete="CASCADE"),
        nullable=False
    )

    attribute_value = Column(
        Text,
        nullable=True
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

    __table_args__ = (
        UniqueConstraint(
            "listing_id",
            "attribute_id",
            name="uq_listing_attribute"
        ),
    )

class BookingStatusHistory(Base):

    __tablename__ = "booking_status_history"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    booking_id = Column(
        UUID(as_uuid=True),
        ForeignKey("bookings.id"),
        nullable=False
    )
    old_status = Column(
        String,
        nullable=False
    )
    new_status = Column(
        String,
        nullable=False
    )
    updated_by = Column(
        UUID(as_uuid=True),
        nullable=True
    )
    remarks = Column(
        Text,
        nullable=True
    )
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )
    booking = relationship(
        "Booking",
        back_populates="status_history"
    )
