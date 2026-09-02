import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy import ForeignKey
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import relationship

from app.db.database import Base


class Event(Base):
    __tablename__ = "events"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    enterprise_id = Column(
        UUID(as_uuid=True),
        ForeignKey("enterprises.id"),
        nullable=True,
        index=True,
    )

    location_id = Column(
        UUID(as_uuid=True),
        ForeignKey("enterprise_locations.id"),
        nullable=True,
        index=True,
    )

    title = Column(String(255), nullable=False, index=True)

    description = Column(Text)

    category = Column(String(100), nullable=False, index=True)

    subcategory = Column(String(100))

    tags = Column(JSONB, default=list)

    organiser_name = Column(String(255))

    organiser_contact = Column(String(255))

    start_date = Column(DateTime, nullable=False)

    end_date = Column(DateTime, nullable=False)

    duration_type = Column(String(20), default="custom", index=True)  # one_day|half_day|custom

    time_zone = Column(String(100), default="Asia/Kolkata")

    registration_cutoff = Column(DateTime)

    primary_image = Column(Text)

    gallery_images = Column(JSONB, default=list)

    videos = Column(JSONB, default=list)

    documents = Column(JSONB, default=list)

    delivery_mode = Column(String(20), default="in_person", index=True)

    venue = Column(JSONB)

    meeting_link = Column(Text)

    meeting_provider = Column(String(50))

    # Pricing
    price = Column(String(50))
    currency = Column(String(3), default="INR")
    ticket_types = Column(JSONB, default=list)

    # Capacity
    capacity = Column(String(50))
    min_participants = Column(String(50))
    max_participants = Column(String(50))
    registration_open_at = Column(DateTime)
    registration_close_at = Column(DateTime)
    custom_fields = Column(JSONB, default=list)

    # Schedule
    sessions = Column(MutableList.as_mutable(JSONB), default=list)

    status = Column(String(20), default="draft", nullable=False, index=True)

    requires_reapproval = Column(Boolean, default=False, nullable=False)

    last_admin_notes = Column(Text)  # Super Admin's latest reject/request_changes message

    is_deleted = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    enterprise = relationship("Enterprise", backref="events")
    location = relationship("EnterpriseLocation", backref="events")

    __table_args__ = (
        Index("ix_events_tenant_enterprise", "tenant_id", "enterprise_id"),
        Index("ix_events_enterprise_location", "enterprise_id", "location_id"),
    )
