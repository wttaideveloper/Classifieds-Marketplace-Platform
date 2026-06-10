from sqlalchemy import Column, String, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid

from app.db.database import Base


class CommunityEcosystem(Base):
    __tablename__ = "community_ecosystem"

    ecosystem_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    provider_name = Column(String(255), nullable=False)

    provider_type = Column(
        String(100),
        nullable=False
    )

    description = Column(Text, nullable=True)

    contact_email = Column(String(255), nullable=True)

    contact_phone = Column(String(50), nullable=True)

    address = Column(Text, nullable=True)

    website = Column(String(500), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )