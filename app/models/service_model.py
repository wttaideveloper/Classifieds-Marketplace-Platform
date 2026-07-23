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

from app.db.database import Base


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

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    enterprise = relationship("Enterprise", backref="services")
    location = relationship("EnterpriseLocation", backref="services")

    __table_args__ = (
        Index("ix_services_tenant_enterprise", "tenant_id", "enterprise_id"),
        Index("ix_services_enterprise_location", "enterprise_id", "location_id"),
    )
