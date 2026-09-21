import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.database import Base


class Training(Base):
    notes_pdf_url = Column(Text, nullable=True)
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
    instructor_name = Column(String(255))
    instructor_bio = Column(Text)
    instructor_role = Column(String(100))
    instructor_photo = Column(Text)  # URL, same convention as primary_image
    instructor_credentials = Column(Text)
    requirements = Column(Text)
    prerequisites = Column(JSONB, default=list)  # course-level prerequisites, e.g. ["Basic Excel", "Prior course X"]
    learning_objectives = Column(JSONB, default=list)
    target_audience = Column(Text)
    level = Column(String(20))  # beginner|intermediate|advanced|all_levels
    language = Column(String(50), default="English")
    subtitle = Column(String(255))  # short tagline, separate from description

    primary_image = Column(Text)
    gallery_images = Column(JSONB, default=list)
    promotional_video = Column(Text)
    documents = Column(JSONB, default=list)
    notes_documents = Column(JSONB, default=list)  # [{title, url}] — instructor-uploaded notes (pre-uploaded to storage; API stores the URL)

    delivery_mode = Column(String(20), default="self_paced", index=True)  # online|physical|hybrid|self_paced (legacy self_paced|instructor_led|blended still accepted, ungated)
    course_type = Column(String(50))  # one_day|workshop|virtual|certification
    duration = Column(String(50))  # e.g. 1 day, 2 weeks, custom
    duration_hours = Column(String(20))  # numeric hours, e.g. "20" — distinct from the free-text `duration` above
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    start_time = Column(String(20))
    end_time = Column(String(20))
    venue = Column(String(255))
    address = Column(Text)
    meeting_link = Column(Text)
    meeting_provider = Column(String(20))  # zoom|google_meet|teams|other
    meeting_id = Column(String(100))
    meeting_passcode = Column(String(50))
    access_information = Column(Text)  # login/access details separate from delivery_instructions
    delivery_instructions = Column(Text)
    offline_access_enabled = Column(Boolean, default=False)
    recurring = Column(JSONB)  # {frequency: daily|weekly|monthly, interval, days_of_week, end_date}
    schedule_exceptions = Column(JSONB, default=list)  # [ISO date strings skipped from the recurrence]
    instructor_notes = Column(Text)  # instructor-facing notes, separate from participant-facing instructor_bio
    session_mode = Column(String(20))  # e.g. live|recorded|hybrid
    check_in = Column(Boolean, default=False)  # enables pass_code/qr_payload self-check-in
    pass_code = Column(String(20))  # server-generated when check_in is enabled
    qr_payload = Column(Text)  # server-generated QR-encodable payload when check_in is enabled
    release_rule = Column(JSONB)  # course-wide content release policy: {mode: 'date'|'enrolment_day'|'immediate', date, days}
    scheduled_publication = Column(DateTime)  # future timestamp to auto-publish this training
    randomise = Column(Boolean, default=False)  # randomise assessment question order
    is_mandatory = Column(Boolean, default=False)  # whole-course mandatory flag (compliance tracking)
    faqs = Column(JSONB, default=list)  # [{question, answer}]
    badges = Column(JSONB, default=list)  # [{name, icon_url}] or plain strings — completion badges
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
    discussions = Column(JSONB, default=list)
    announcements = Column(JSONB, default=list)
    moderation_history = Column(JSONB, default=list)
    last_admin_notes = Column(Text)

    form_configuration_id = Column(UUID(as_uuid=True), ForeignKey("training_form_configurations.id"), nullable=True, index=True)
    form_configuration_version_id = Column(UUID(as_uuid=True), ForeignKey("training_form_configuration_versions.id"), nullable=True, index=True)
    custom_values = Column(JSONB, default=list)

    status = Column(String(20), default="draft", nullable=False, index=True)
    moderation_status = Column(String(20), default="draft")  # draft|pending|approved|rejected|changes_requested
    rejection_reason = Column(Text)
    published_at = Column(DateTime)
    approved_at = Column(DateTime)
    archived_at = Column(DateTime)
    suspended_at = Column(DateTime)
    cancelled_at = Column(DateTime)
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
    status = Column(String(20), default="enrolled")  # enrolled|pending_approval|cancelled|waitlisted|expired|attended
    coupon_code = Column(String(50))
    access_expires_at = Column(DateTime)
    qr_code = Column(String(255), unique=True, index=True)  # server-generated at enrolment time
    checked_in_at = Column(DateTime, nullable=True)
    checked_out_at = Column(DateTime, nullable=True)
    checked_in_by = Column(UUID(as_uuid=True), nullable=True)  # user id of the admin/provider who scanned them in
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
    files = Column(JSONB, default=list)  # [{url, name, type}] — multi-media submission (image|video|document|link)
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
    lesson_positions = Column(JSONB, default=dict)  # {lesson_id: {section_id, position_seconds, duration_seconds, last_accessed_at}} — video/lesson resume tracking
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
    attendance = Column(JSONB, default=list)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class TrainingReview(Base):
    __tablename__ = "training_reviews"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    training_id = Column(UUID(as_uuid=True), ForeignKey("trainings.id"), nullable=False, index=True)
    participant_email = Column(String(255), nullable=False)
    rating = Column(String(20), nullable=False)
    comment = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_training_reviews_training_email", "training_id", "participant_email", unique=True),
    )


class TrainingWishlistItem(Base):
    __tablename__ = "training_wishlist_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    training_id = Column(UUID(as_uuid=True), ForeignKey("trainings.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_training_wishlist_user_training", "user_id", "training_id", unique=True),
    )
