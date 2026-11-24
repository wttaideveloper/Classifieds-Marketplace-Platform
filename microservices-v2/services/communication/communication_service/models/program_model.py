"""v2 communication: READ-ONLY replica of Program (kept current by mskit.replication).
Foreign keys to tables this service does not hold were dropped; columns, types and indexes are the monolith's."""
import uuid
from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import relationship
from communication_service.db.database import Base

class Program(Base):
    __tablename__ = "programs"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    enterprise_id = Column(UUID(as_uuid=True), ForeignKey("enterprises.id"), nullable=False, index=True)
    location_id = Column(UUID(as_uuid=True), ForeignKey("enterprise_locations.id"), nullable=True, index=True)
    title = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    category = Column(String(100), nullable=False, index=True)
    provider_id = Column(UUID(as_uuid=True), nullable=True)
    duration_weeks = Column(String(50))
    eligibility = Column(JSONB, default=dict)
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    enrolment_start = Column(DateTime)
    enrolment_end = Column(DateTime)
    enrol_type = Column(String(20), default="fixed")
    delivery_mode = Column(String(20), default="offline", index=True)
    price = Column(String(50))
    currency = Column(String(3), default="INR")
    capacity = Column(String(50))
    phases = Column(MutableList.as_mutable(JSONB), default=list)
    goals = Column(JSONB, default=dict)
    last_admin_notes = Column(Text)
    custom_values = Column(JSONB, default=list)
    form_configuration_id = Column(UUID(as_uuid=True),  nullable=True, index=True)
    form_configuration_version_id = Column(UUID(as_uuid=True),  nullable=True, index=True)
    status = Column(String(20), default="draft", nullable=False, index=True)
    is_deleted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    enterprise = relationship("Enterprise", backref="programs")
    __table_args__ = (Index("ix_programs_tenant_enterprise", "tenant_id", "enterprise_id"),)




