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

    full_name = Column(String(255), nullable=False)

    business_email = Column(
        String(255),
        unique=True,
        index=True,
        nullable=False
    )

    mobile_number = Column(String(20), nullable=False)

    business_name = Column(String(255), nullable=False)

    password = Column(String(255), nullable=False)

    accept_terms = Column(Boolean, default=False, nullable=False)

    accept_privacy_policy = Column(
        Boolean,
        default=False,
        nullable=False
    )

    reset_token = Column(String(255), nullable=True)

    reset_token_expiry = Column(DateTime, nullable=True)

    is_email_verified = Column(
        Boolean,
        default=False,
        nullable=False
    )

    verification_token = Column(String(255), nullable=True)

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

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    merchant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True
    )

    business_name = Column(String(255), nullable=False)

    business_description = Column(Text, nullable=True)

    primary_category = Column(String(255), nullable=False)

    subcategory = Column(String(255), nullable=True)

    business_email = Column(String(255), nullable=False)

    phone_number = Column(String(20), nullable=False)

    full_address = Column(Text, nullable=False)

    city = Column(String(100), nullable=False)

    state = Column(String(100), nullable=False)

    zip_code = Column(String(20), nullable=False)

    country = Column(String(100), nullable=False)

    latitude = Column(Float, nullable=True)

    longitude = Column(Float, nullable=True)

    business_logo = Column(String(500), nullable=True)

    banner_image = Column(String(500), nullable=True)

    gallery_images = Column(JSON, default=list)

    operating_hours = Column(JSON, default=dict)

    business_type = Column(String(50), nullable=False)

    cancellation_policy = Column(Text, nullable=True)

    refund_policy = Column(Text, nullable=True)

    merchant_terms_of_service = Column(Text, nullable=True)

    website_url = Column(String(500), nullable=True)

    social_media_links = Column(JSON, default=dict)

    additional_contact_numbers = Column(JSON, default=list)

    short_tagline = Column(String(255), nullable=True)

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

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    merchant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True
    )

    business_name = Column(String(255), nullable=True)

    business_description = Column(Text, nullable=True)

    primary_category = Column(String(255), nullable=True)

    subcategory = Column(String(255), nullable=True)

    business_email = Column(String(255), nullable=True)

    phone_number = Column(String(20), nullable=True)

    full_address = Column(Text, nullable=True)

    city = Column(String(100), nullable=True)

    state = Column(String(100), nullable=True)

    zip_code = Column(String(20), nullable=True)

    country = Column(String(100), nullable=True)

    latitude = Column(Float, nullable=True)

    longitude = Column(Float, nullable=True)

    business_logo = Column(String(500), nullable=True)

    banner_image = Column(String(500), nullable=True)

    gallery_images = Column(JSON, default=list)

    operating_hours = Column(JSON, default=dict)

    business_type = Column(String(50), nullable=True)

    cancellation_policy = Column(Text, nullable=True)

    refund_policy = Column(Text, nullable=True)

    merchant_terms_of_service = Column(Text, nullable=True)

    website_url = Column(String(500), nullable=True)

    social_media_links = Column(JSON, default=dict)

    additional_contact_numbers = Column(JSON, default=list)

    short_tagline = Column(String(255), nullable=True)

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

    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id"),
        nullable=False
    )

    listing_type = Column(
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

    category_id = Column(
        UUID(as_uuid=True),
        ForeignKey("categories.id"),
        nullable=True
    )

    subcategory_id = Column(
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
    stock_quantity = Column(Integer, nullable=True)

    sku = Column(String, nullable=True)

    weight = Column(Float, nullable=True)

    # SERVICE
    duration = Column(String, nullable=True)

    service_mode = Column(String, nullable=True)

    availability = Column(String, nullable=True)

    schedule = Column(String, nullable=True)

    # EVENT / TRAINING / PROGRAM
    start_date = Column(DateTime, nullable=True)

    end_date = Column(DateTime, nullable=True)

    capacity = Column(Integer, nullable=True)

    location = Column(String, nullable=True)

    is_online = Column(Boolean, default=False)

    registration_deadline = Column(
        DateTime,
        nullable=True
    )

    rejection_reason = Column(Text, nullable=True)

    rejected_at = Column(DateTime, nullable=True)

    suspension_reason = Column(Text, nullable=True)

    suspended_at = Column(DateTime, nullable=True)

    approved_at = Column(DateTime, nullable=True)

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
        foreign_keys=[category_id]
    )

    subcategory = relationship(
        "Category",
        foreign_keys=[subcategory_id]
    )
    bookings = relationship(
        "Booking",
        back_populates="listing"
    )
    wishlists = relationship(
        "Wishlist",
        back_populates="listing",
        cascade="all, delete"
    )
    media = relationship(
        "Media",
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

    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id"),
        nullable=False
    )

    listing_type = Column(
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

    category_id = Column(
        UUID(as_uuid=True),
        ForeignKey("categories.id"),
        nullable=True
    )

    subcategory_id = Column(
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

    stock_quantity = Column(Integer, nullable=True)

    sku = Column(String, nullable=True)

    weight = Column(Float, nullable=True)

    duration = Column(String, nullable=True)

    service_mode = Column(String, nullable=True)

    availability = Column(String, nullable=True)

    schedule = Column(String, nullable=True)

    start_date = Column(DateTime, nullable=True)

    end_date = Column(DateTime, nullable=True)

    capacity = Column(Integer, nullable=True)

    location = Column(String, nullable=True)

    is_online = Column(Boolean, default=False)

    registration_deadline = Column(
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
        foreign_keys=[category_id]
    )

    subcategory = relationship(
        "Category",
        foreign_keys=[subcategory_id]
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
