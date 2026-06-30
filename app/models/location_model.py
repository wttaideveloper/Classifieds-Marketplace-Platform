import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.database import Base


class EnterpriseLocation(Base):
    __tablename__ = "enterprise_locations"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    enterprise_id = Column(
        UUID(as_uuid=True),
        ForeignKey("enterprises.id"),
        nullable=False,
        index=True,
    )

    location_name = Column(String(255), nullable=False)

    address_line_1 = Column(Text)

    address_line_2 = Column(Text)

    city = Column(String(100), index=True)

    state = Column(String(100))

    country = Column(String(100))

    postal_code = Column(String(20))

    phone = Column(String(30))

    email = Column(String(255))

    latitude = Column(Float)

    longitude = Column(Float)

    status = Column(String(20), default="active", nullable=False, index=True)

    is_deleted = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    enterprise = relationship("Enterprise", backref="locations")

    __table_args__ = (
        Index("ix_enterprise_locations_enterprise_status", "enterprise_id", "status"),
    )
