import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.db.database import Base


class Enterprise(Base):
    __tablename__ = "enterprises"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    business_short_name = Column(String(100), nullable=False, index=True)

    business_legal_name = Column(String(255), nullable=False)

    business_description = Column(Text)

    business_email = Column(String(255), unique=True, nullable=False)

    business_phone = Column(String(30))

    registered_address = Column(Text)

    business_address = Column(Text)

    communication_address = Column(Text)

    suite_unit = Column(String(100))

    logo_url = Column(Text)

    banner_url = Column(Text)

    business_images = Column(Text)

    registration_number = Column(String(100))

    business_category = Column(String(100), index=True)

    website = Column(Text)

    website_url = Column(Text)

    year_founded = Column(Integer)

    primary_contact_name = Column(String(255))

    primary_contact_title = Column(String(100))

    secondary_email = Column(String(255))

    secondary_phone = Column(String(30))

    brand_color = Column(String(20))

    tagline = Column(String(255))

    status = Column(String(20), default="draft", nullable=False, index=True)

    is_deleted = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_enterprises_tenant_status", "tenant_id", "status"),
    )
