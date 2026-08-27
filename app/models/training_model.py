import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.database import Base


class Training(Base):
    __tablename__ = "trainings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    enterprise_id = Column(UUID(as_uuid=True), ForeignKey("enterprises.id"), nullable=False, index=True)
    location_id = Column(UUID(as_uuid=True), ForeignKey("enterprise_locations.id"), nullable=True, index=True)

    title = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    category = Column(String(100), nullable=False, index=True)
    subcategory = Column(String(100))
    tags = Column(JSONB, default=list)
    instructor_id = Column(UUID(as_uuid=True), nullable=True)
    requirements = Column(Text)

    primary_image = Column(Text)
    gallery_images = Column(JSONB, default=list)
    promotional_video = Column(Text)
    documents = Column(JSONB, default=list)

    delivery_mode = Column(String(20), default="self_paced", index=True)  # self_paced|instructor_led|blended
    course_type = Column(String(50))  # one_day|workshop|virtual|certification
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    enrolment_start = Column(DateTime)
    enrolment_end = Column(DateTime)
    time_zone = Column(String(100), default="Asia/Kolkata")
    capacity = Column(String(50))
    price = Column(String(50))
    currency = Column(String(3), default="INR")
    promo_price = Column(String(50))
    coupon_code = Column(String(50))
    requires_approval = Column(Boolean, default=False)
    access_duration_days = Column(String(20))  # e.g. "30" days expiry

    # JSONB builders
    sections = Column(JSONB, default=list)  # [{id, title, order, lessons:[]}]
    assessments = Column(JSONB, default=list)
    assignments = Column(JSONB, default=list)

    status = Column(String(20), default="draft", nullable=False, index=True)
    is_deleted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    enterprise = relationship("Enterprise", backref="trainings")
    location = relationship("EnterpriseLocation", backref="trainings")

    __table_args__ = (
        Index("ix_trainings_tenant_enterprise", "tenant_id", "enterprise_id"),
    )


class TrainingEnrolment(Base):
    __tablename__ = "training_enrolments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    training_id = Column(UUID(as_uuid=True), ForeignKey("trainings.id"), nullable=False, index=True)
    participant_name = Column(String(255), nullable=False)
    participant_email = Column(String(255), nullable=False, index=True)
    group_enrol = Column(Boolean, default=False)
    status = Column(String(20), default="enrolled")  # enrolled|pending_approval|cancelled|waitlisted|expired
    coupon_code = Column(String(50))
    access_expires_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class TrainingWaitlist(Base):
    __tablename__ = "training_waitlist"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    training_id = Column(UUID(as_uuid=True), ForeignKey("trainings.id"), nullable=False, index=True)
    participant_name = Column(String(255), nullable=False)
    participant_email = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class TrainingAssessmentSubmission(Base):
    __tablename__ = "training_assessment_submissions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    training_id = Column(UUID(as_uuid=True), ForeignKey("trainings.id"), nullable=False, index=True)
    assessment_id = Column(String(255), nullable=False, index=True)
    participant_email = Column(String(255), nullable=False, index=True)
    answers = Column(JSONB, default=list)
    score = Column(String(20))
    passed = Column(Boolean, default=False)
    submitted_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class TrainingAssignmentSubmission(Base):
    __tablename__ = "training_assignment_submissions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    training_id = Column(UUID(as_uuid=True), ForeignKey("trainings.id"), nullable=False, index=True)
    assignment_id = Column(String(255), nullable=False, index=True)
    participant_email = Column(String(255), nullable=False, index=True)
    file_url = Column(Text)
    submission_text = Column(Text)
    grade = Column(String(20))
    feedback = Column(Text)
    submitted_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class TrainingProgress(Base):
    __tablename__ = "training_progress"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    training_id = Column(UUID(as_uuid=True), ForeignKey("trainings.id"), nullable=False, index=True)
    participant_email = Column(String(255), nullable=False, index=True)
    sections_completed = Column(JSONB, default=list)
    lessons_completed = Column(JSONB, default=list)
    overall_percent = Column(String(20), default="0")
    certificate_url = Column(Text)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime)
    last_accessed_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (Index("ix_training_progress_training_email", "training_id", "participant_email", unique=True),)


class TrainingOrder(Base):
    __tablename__ = "training_orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    training_id = Column(UUID(as_uuid=True), ForeignKey("trainings.id"), nullable=False, index=True)
    participant_name = Column(String(255), nullable=False)
    participant_email = Column(String(255), nullable=False, index=True)
    quantity = Column(String(20), default="1")
    amount = Column(String(50))
    currency = Column(String(10), default="INR")
    payment_status = Column(String(20), default="confirmed", index=True)
    status = Column(String(20), default="confirmed", index=True)
    coupon_code = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class TrainingLiveSession(Base):
    __tablename__ = "training_live_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    training_id = Column(UUID(as_uuid=True), ForeignKey("trainings.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    scheduled_at = Column(DateTime, nullable=False)
    duration_minutes = Column(String(20))
    meeting_link = Column(Text)
    meeting_provider = Column(String(50), default="zoom")
    status = Column(String(20), default="scheduled")
    recording_url = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
