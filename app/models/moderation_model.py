import enum
import uuid

from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.database import Base


class ModerationEntityType(str, enum.Enum):
    Business = "Business"
    Listing = "Listing"
    Review = "Review"
    Blog = "Blog"
    Merchant = "Merchant"


class ReportedEntityType(str, enum.Enum):
    Listing = "Listing"
    Review = "Review"
    Blog = "Blog"


class ReportStatus(str, enum.Enum):
    Pending = "Pending"
    Reviewed = "Reviewed"
    Resolved = "Resolved"


class ModerationLog(Base):
    __tablename__ = "moderation_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type = Column(String(50), nullable=False, index=True)
    entity_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    old_status = Column(String(50), nullable=False)
    new_status = Column(String(50), nullable=False)
    moderated_by = Column(UUID(as_uuid=True), nullable=False, index=True)
    remarks = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ReportedContent(Base):
    __tablename__ = "reported_content"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reported_by = Column(UUID(as_uuid=True), nullable=False, index=True)
    entity_type = Column(String(50), nullable=False, index=True)
    entity_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    reason = Column(Text, nullable=False)
    report_status = Column(String(50), default=ReportStatus.Pending.value, nullable=False, index=True)
    reviewed_by = Column(UUID(as_uuid=True), nullable=True)
    remarks = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
