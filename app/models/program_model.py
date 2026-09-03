import uuid
from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from app.db.database import Base

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
    phases = Column(JSONB, default=list)
    goals = Column(JSONB, default=dict)
    status = Column(String(20), default="draft", nullable=False, index=True)
    is_deleted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    enterprise = relationship("Enterprise", backref="programs")
    __table_args__ = (Index("ix_programs_tenant_enterprise", "tenant_id", "enterprise_id"),)

class ProgramEnrolment(Base):
    __tablename__ = "program_enrolments"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    program_id = Column(UUID(as_uuid=True), ForeignKey("programs.id"), nullable=False, index=True)
    participant_name = Column(String(255), nullable=False)
    participant_email = Column(String(255), nullable=False, index=True)
    status = Column(String(20), default="enrolled")
    new_end_date = Column(DateTime)
    withdrawal_reason = Column(Text)
    participant_goals = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class ProgramCheckin(Base):
    __tablename__ = "program_checkins"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    program_id = Column(UUID(as_uuid=True), ForeignKey("programs.id"), nullable=False, index=True)
    participant_email = Column(String(255), nullable=False)
    phase_id = Column(String(100))
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class ProgramSurvey(Base):
    __tablename__ = "program_surveys"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    program_id = Column(UUID(as_uuid=True), ForeignKey("programs.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    questions = Column(JSONB, default=list)
    created_by = Column(String(255))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class ProgramReview(Base):
    __tablename__ = "program_reviews"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    program_id = Column(UUID(as_uuid=True), ForeignKey("programs.id"), nullable=False, index=True)
    participant_email = Column(String(255), nullable=False)
    rating = Column(String(20), nullable=False)
    comment = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
