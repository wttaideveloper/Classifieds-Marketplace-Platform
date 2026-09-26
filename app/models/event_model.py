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

    title = Column(String(255), nullable=True, index=True)  # incomplete template draft

    description = Column(Text)

    category = Column(String(100), nullable=True, index=True)

    subcategory = Column(String(100))

    tags = Column(JSONB, default=list)

    organiser_name = Column(String(255))

    organiser_contact = Column(String(255))

    start_date = Column(DateTime, nullable=True)

    end_date = Column(DateTime, nullable=True)

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
    pricing_type = Column(String(20), default="free", nullable=False, index=True)
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
    custom_values = Column(JSONB, default=list)

    form_configuration_id = Column(UUID(as_uuid=True), ForeignKey("event_form_configurations.id"), nullable=True, index=True)
    form_configuration_version_id = Column(UUID(as_uuid=True), ForeignKey("event_form_configuration_versions.id"), nullable=True, index=True)

    # Schedule
    sessions = Column(MutableList.as_mutable(JSONB), default=list)

    # Configurable event (Phase 2.2). Both are NULL for legacy events, which are resolved on read from
    # their actual behaviour (see app/utils/event_modules.py) instead of being backfilled.
    event_type = Column(String(30), nullable=True)  # conference|workshop|marathon|camp|private_function|webinar|other
    modules = Column(JSONB, nullable=True)  # {registration, tickets, sessions, check_in, online_meeting, custom_questions, meals, accommodation: bool}
    # Meal options (Phase 2.6): {"options": [{id, name, description?, date?, active}]}. NULL for every event that has
    # none (all legacy events). Whether meals are ON is modules.meals, never stored here; options an update stops listing
    # stay with active=false so registrations that selected them keep resolving.
    meals = Column(JSONB, nullable=True)
    # Accommodation options (Phase 2.7): {"options": [{id, name, description?, active}]}. Same shape and rules as meals: NULL
    # for every event that has none (all legacy events); whether accommodation is ON is modules.accommodation, never stored
    # here; options an update stops listing stay with active=false so registrations that selected them keep resolving.
    accommodation = Column(JSONB, nullable=True)

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
