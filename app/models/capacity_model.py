from sqlalchemy import (
    Column,
    Integer,
    Boolean,
    Text,
    DateTime,
    Enum,
    ForeignKey
)

from app.db.database import Base
from sqlalchemy.dialects.postgresql import UUID

from datetime import datetime
from enum import Enum as PyEnum

import uuid


class CapacityStatus(str, PyEnum):

    OPEN = "OPEN"
    FULL = "FULL"
    CLOSED = "CLOSED"


class ListingCapacity(Base):

    __tablename__ = "listing_capacity"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    listing_id = Column(
        UUID(as_uuid=True),
        ForeignKey("merchant_listings.id"),
        nullable=False,
        unique=True
    )

    total_capacity = Column(
        Integer,
        nullable=False
    )

    booked_capacity = Column(
        Integer,
        default=0
    )

    available_capacity = Column(
        Integer,
        nullable=False
    )

    waitlist_enabled = Column(
        Boolean,
        default=False
    )

    capacity_status = Column(
        Enum(CapacityStatus),
        default=CapacityStatus.OPEN
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


class CapacityHistory(Base):

    __tablename__ = "capacity_history"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    listing_id = Column(
        UUID(as_uuid=True),
        ForeignKey("merchant_listings.id"),
        nullable=False
    )

    old_capacity = Column(Integer)

    new_capacity = Column(Integer)

    updated_by = Column(
        UUID(as_uuid=True),
        nullable=True
    )

    remarks = Column(Text)

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )