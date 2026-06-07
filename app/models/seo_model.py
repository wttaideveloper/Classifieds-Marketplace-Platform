import enum
import uuid

from sqlalchemy import Column, DateTime, Enum, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.database import Base


class SeoEntityType(str, enum.Enum):
    Business = "Business"
    Listing = "Listing"
    Blog = "Blog"


class SitemapChangeFrequency(str, enum.Enum):
    Daily = "Daily"
    Weekly = "Weekly"
    Monthly = "Monthly"


class SeoMetadata(Base):
    __tablename__ = "seo_metadata"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type = Column(Enum(SeoEntityType), nullable=False, index=True)
    entity_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    meta_title = Column(String(255), nullable=False)
    meta_description = Column(Text, nullable=False)
    meta_keywords = Column(Text, nullable=True)
    canonical_url = Column(String(500), nullable=True)
    og_title = Column(String(255), nullable=True)
    og_description = Column(Text, nullable=True)
    og_image = Column(String(500), nullable=True)
    slug = Column(String(255), nullable=False, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", name="uq_seo_metadata_entity"),
    )


class SitemapEntry(Base):
    __tablename__ = "sitemap_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type = Column(Enum(SeoEntityType), nullable=False, index=True)
    entity_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    sitemap_url = Column(String(500), nullable=False, unique=True, index=True)
    last_modified = Column(DateTime(timezone=True), server_default=func.now())
    priority = Column(Numeric(2, 1), nullable=False, default=0.5)
    change_frequency = Column(
        Enum(SitemapChangeFrequency),
        nullable=False,
        default=SitemapChangeFrequency.Weekly,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", name="uq_sitemap_entry_entity"),
    )
