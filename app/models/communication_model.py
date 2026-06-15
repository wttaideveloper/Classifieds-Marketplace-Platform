from sqlalchemy import Column, String, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID
from app.db.database import Base

import uuid
from datetime import datetime


class Communication(Base):
    __tablename__ = "communications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    customer_name = Column(String(255), nullable=False)
    customer_email = Column(String(255), nullable=False)

    subject = Column(String(255), nullable=False)

    message = Column(Text, nullable=False)

    status = Column(
        String(50),
        nullable=False,
        default="OPEN"
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