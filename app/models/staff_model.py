import uuid
from sqlalchemy import Column, String, DateTime, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.db.database import Base
import enum

class StaffStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    PENDING = "pending"

class InvitationStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"

class Staff(Base):
    __tablename__ = "staffs"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    merchant_id = Column(
        UUID(as_uuid=True),
        nullable=False
    )

    first_name = Column(
        String(100),
        nullable=False
    )

    last_name = Column(
        String(100),
        nullable=False
    )

    email = Column(
        String(255),
        unique=True,
        nullable=False
    )

    phone_number = Column(
        String(20),
        nullable=False
    )

    role_id = Column(
        UUID(as_uuid=True),
        nullable=False
    )

    staff_status = Column(
        Enum(StaffStatus),
        default=StaffStatus.PENDING,
        nullable=False
    )

    invited_by = Column(
        UUID(as_uuid=True),
        nullable=False
    )

    joined_at = Column(
        DateTime(timezone=True),
        nullable=True
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

import uuid
import enum

from sqlalchemy import (
    Column,
    String,
    DateTime,
    Enum
)

class StaffInvitation(Base):
    __tablename__ = "staff_invitations"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    merchant_id = Column(
        UUID(as_uuid=True),
        nullable=False
    )

    email = Column(
        String(255),
        nullable=False
    )

    invitation_token = Column(
        String(500),
        nullable=False
    )

    invitation_status = Column(
        Enum(InvitationStatus),
        nullable=False
    )

    expires_at = Column(
        DateTime(timezone=True),
        nullable=False
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

class Role(Base):
    __tablename__ = "roles"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    role_name = Column(
        String(100),
        nullable=False
    )