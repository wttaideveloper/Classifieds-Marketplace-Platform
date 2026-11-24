# v2 booking: read-only replica of catalog's services table (kept current by mskit.replication). Reviews stay in catalog.
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from booking_service.db.database import Base


class Service(Base):
    __tablename__ = "services"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    enterprise_id = Column(
        UUID(as_uuid=True),
        ForeignKey("enterprises.id"),
        nullable=False,
        index=True,
    )

    location_id = Column(
        UUID(as_uuid=True),
        ForeignKey("enterprise_locations.id"),
        nullable=True,
        index=True,
    )

    service_name = Column(String(255), nullable=False, index=True)

    service_description = Column(Text)

    service_category = Column(String(100), nullable=False, index=True)

    service_type = Column(String(100))

    banner_image = Column(Text)

    service_price = Column(Float, nullable=False)

    duration = Column(Integer, nullable=False)

    availability_status = Column(Boolean, default=True)

    availability_schedule = Column(JSONB)

    service_status = Column(Boolean, default=True)

    status = Column(String(20), default="draft", nullable=False, index=True)

    is_deleted = Column(Boolean, default=False, nullable=False)

    max_participants = Column(Integer)

    provider_name = Column(String(255))

    provider_user_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    instructor_name = Column(String(255))

    delivery_format = Column(String(100))

    package_price = Column(Float)

    currency = Column(String(3), default="USD")

    cancellation_policy = Column(Text)

    # --- booking (docs/service-bookings.md) ---
    delivery_modes = Column(JSONB)                          # ["in_person"] | ["virtual"] | ["hybrid"] | ["in_person", "virtual"]; null = worked out from delivery_format
    booking_enabled = Column(Boolean, default=True, nullable=False, server_default="true")
    requires_approval = Column(Boolean, default=False, nullable=False, server_default="false")   # a new booking waits for an admin to confirm it
    time_zone = Column(String(64), default="Asia/Kolkata")  # IANA zone the weekly schedule is written in
    # {"free_cancellation_hours": 24, "refund_percent_before": 100, "refund_percent_after": 0, "allow_request_after_window": true}
    cancellation_rules = Column(JSONB)
    venue_instructions = Column(Text)                       # arrival / check-in instructions (the address is the service's location)
    meeting_url = Column(Text)                              # a pasted link (Zoom, Google Meet, ...)
    meeting_provider = Column(String(30))
    meeting_passcode = Column(String(100))
    meeting_instructions = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    enterprise = relationship("Enterprise", backref="services")
    location = relationship("EnterpriseLocation", backref="services")

    __table_args__ = (
        Index("ix_services_tenant_enterprise", "tenant_id", "enterprise_id"),
        Index("ix_services_enterprise_location", "enterprise_id", "location_id"),
    )


