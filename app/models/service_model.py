import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
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

    enterprise_id = Column(
        UUID(as_uuid=True),
        ForeignKey("enterprises.id"),
        nullable=False,
    )

    service_name = Column(String(255), nullable=False)

    service_description = Column(Text)

    service_category = Column(String(100), nullable=False)

    service_price = Column(Float, nullable=False)

    duration = Column(Integer, nullable=False)

    availability_status = Column(Boolean, default=True)

    service_status = Column(Boolean, default=True)

    max_participants = Column(Integer)

    provider_name = Column(String(255))

    instructor_name = Column(String(255))

    delivery_format = Column(String(100))

    package_price = Column(Float)

    currency = Column(String(3), default="USD")

    cancellation_policy = Column(Text)

    availability_schedule = Column(JSONB)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    enterprise = relationship("Enterprise", backref="services")
