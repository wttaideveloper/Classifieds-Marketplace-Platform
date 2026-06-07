import uuid
from enum import Enum

from sqlalchemy import (
    Column,
    String,
    Boolean,
    DateTime,
    Text,
    Enum as SqlEnum
)
from sqlalchemy.dialects.postgresql import UUID

from app.db.database import Base
from datetime import datetime


class CalendarProvider(str, Enum):
    GOOGLE = "google"
    OUTLOOK = "outlook"

class EventStatusEnum(str, Enum):
    SCHEDULED = "scheduled"
    UPDATED = "updated"
    CANCELLED = "cancelled"

class CalendarIntegration(Base):
    __tablename__ = "calendar_integrations"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    merchant_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        index=True
    )

    provider = Column(
        SqlEnum(CalendarProvider),
        nullable=False
    )

    provider_account_id = Column(
        String,
        nullable=False
    )

    access_token = Column(
        Text,
        nullable=False
    )

    refresh_token = Column(
        Text,
        nullable=False
    )

    token_expiry = Column(
        DateTime,
        nullable=False
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

class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    booking_id = Column(
        UUID(as_uuid=True),
        nullable=False
    )

    merchant_id = Column(
        UUID(as_uuid=True),
        nullable=False
    )

    provider = Column(
        SqlEnum(CalendarProvider),
        nullable=False
    )

    external_event_id = Column(
        String,
        nullable=False
    )

    event_title = Column(
        String,
        nullable=False
    )

    start_time = Column(
        DateTime,
        nullable=False
    )

    end_time = Column(
        DateTime,
        nullable=False
    )

    meeting_link = Column(
        String,
        nullable=True
    )

    event_status = Column(
        SqlEnum(EventStatusEnum),
        nullable=False,
        default=EventStatusEnum.SCHEDULED
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