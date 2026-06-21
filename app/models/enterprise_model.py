import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.db.database import Base


class Enterprise(Base):
    __tablename__ = "enterprises"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    business_short_name = Column(String(100), nullable=False)

    business_legal_name = Column(String(255), nullable=False)

    business_description = Column(Text)

    business_email = Column(String(255), unique=True, nullable=False)

    business_phone = Column(String(30))

    registered_address = Column(Text)

    business_address = Column(Text)

    communication_address = Column(Text)

    suite_unit = Column(String(100))

    logo_url = Column(Text)

    business_images = Column(Text)

    registration_number = Column(String(100))

    business_category = Column(String(100))

    website_url = Column(Text)

    year_founded = Column(Integer)

    primary_contact_name = Column(String(255))

    primary_contact_title = Column(String(100))

    secondary_email = Column(String(255))

    secondary_phone = Column(String(30))

    brand_color = Column(String(20))

    tagline = Column(String(255))

    status = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
