from sqlalchemy import (
    Column,
    String,
    Boolean,
    DateTime,
    Text,
    Enum
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from uuid import uuid4
import enum

from app.db.database import Base


class MeetingProviderEnum(str, enum.Enum):
    ZOOM = "Zoom"
    GOOGLE_MEET = "GoogleMeet"
    TEAMS = "Teams"


class MeetingStatusEnum(str, enum.Enum):
    SCHEDULED = "Scheduled"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"


class MeetingIntegration(Base):
    __tablename__ = "meeting_integrations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    merchant_id = Column(UUID(as_uuid=True), nullable=False)

    provider = Column(
        Enum(MeetingProviderEnum),
        nullable=False
    )

    provider_account_id = Column(String)

    access_token = Column(Text)

    refresh_token = Column(Text)

    token_expiry = Column(DateTime)

    is_active = Column(
        Boolean,
        default=True
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )


class Meeting(Base):
    __tablename__ = "meetings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    booking_id = Column(UUID(as_uuid=True), nullable=False)

    merchant_id = Column(UUID(as_uuid=True), nullable=False)

    provider = Column(
        Enum(MeetingProviderEnum),
        nullable=False
    )

    external_meeting_id = Column(String)

    meeting_title = Column(String)

    meeting_link = Column(String)

    meeting_password = Column(String)

    start_time = Column(DateTime)

    end_time = Column(DateTime)

    meeting_status = Column(
        Enum(MeetingStatusEnum),
        default=MeetingStatusEnum.SCHEDULED
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )