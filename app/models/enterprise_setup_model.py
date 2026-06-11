from sqlalchemy import Column, String, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
from app.db.database import Base

class EnterpriseSetup(Base):
    __tablename__ = "enterprise_setup"

    enterprise_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    organization_name = Column(String(255), nullable=False)
    organization_code = Column(String(100), unique=True, nullable=False)

    industry = Column(String(100))
    website = Column(String(255))

    email = Column(String(255))
    phone = Column(String(20))

    address = Column(Text)

    company_size = Column(String(50))

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )