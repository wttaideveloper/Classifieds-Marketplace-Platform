import uuid

from sqlalchemy import (
    Column,
    String,
    Text,
    Boolean,
    Float,
    Integer,
    ForeignKey
)

from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.database import Base


class Service(Base):
    __tablename__ = "services"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    enterprise_id = Column(
        UUID(as_uuid=True),
        ForeignKey("enterprises.id"),
        nullable=False
    )

    service_name = Column(
        String(255),
        nullable=False
    )

    service_description = Column(Text)

    service_category = Column(
        String(100),
        nullable=False
    )

    service_price = Column(
        Float,
        nullable=False
    )

    duration = Column(
        Integer,
        nullable=False
    )  # minutes

    availability_status = Column(
        Boolean,
        default=True
    )

    service_status = Column(
        Boolean,
        default=True
    )

    enterprise = relationship(
        "Enterprise",
        backref="services"
    )