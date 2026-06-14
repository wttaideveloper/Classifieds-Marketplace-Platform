from sqlalchemy import Column, String, DateTime
from sqlalchemy.dialects.postgresql import UUID
import uuid
from app.db.database import Base

class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    title = Column(String, nullable=False)

    description = Column(String, nullable=True)

    customer_name = Column(String, nullable=False)

    customer_email = Column(String, nullable=False)

    start_time = Column(DateTime, nullable=False)

    end_time = Column(DateTime, nullable=False)

    status = Column(
        String,
        nullable=False,
        default="scheduled"
    )