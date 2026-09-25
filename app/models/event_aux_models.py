import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.database import Base


class EventRegistration(Base):
    __tablename__ = "event_registrations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False, index=True)
    participant_name = Column(String(255), nullable=False)
    participant_email = Column(String(255), nullable=False, index=True)
    custom_fields = Column(JSONB, default=dict)
    ticket_type_id = Column(String(100))
    status = Column(String(20), default="confirmed", index=True)  # confirmed|cancelled|attended|no_show..
    qr_code = Column(String(255), unique=True, index=True)
    checked_in_at = Column(DateTime, nullable=True)
    checked_in_by = Column(UUID(as_uuid=True), nullable=True)
    checked_out_at = Column(DateTime, nullable=True)
    session_id = Column(String(100), nullable=True)  # For per-session attendance tracking
    # How the registration was made: "walk_in" (organizer at the venue, Phase 2.5). NULL = online — every row
    # that existed before this column, and every self-registration/checkout, which leave it unset.
    registration_source = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    event = relationship("Event", backref="registrations")


class EventSessionAttendance(Base):
    """One attendee's attendance at one session of an Event (Phase 2.4).

    Sessions live inside ``Event.sessions`` (JSONB), not in a table, so ``session_id`` is an id inside that
    list and deliberately has no foreign key: the service validates it against the Event's own sessions.
    An attendee can attend any number of sessions; ``UNIQUE(event_id, registration_id, session_id)`` keeps it
    to one row per session. Event-level check-in (``EventRegistration.status`` / ``checked_in_at``) is a
    separate, unchanged concept.

    State is carried by the timestamps: a row exists = checked in; ``checked_out_at`` set = checked out.
    Undoing a check-in deletes the row (the audit trail keeps the history).
    """

    __tablename__ = "event_session_attendance"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    registration_id = Column(UUID(as_uuid=True), ForeignKey("event_registrations.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(String(100), nullable=False)  # id inside Event.sessions
    checked_in_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    checked_in_by = Column(UUID(as_uuid=True), nullable=True)  # operator; like EventRegistration.checked_in_by, not an FK
    checked_out_at = Column(DateTime, nullable=True)
    checked_out_by = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        # Also serves (event_id, registration_id) lookups through its leading columns.
        UniqueConstraint("event_id", "registration_id", "session_id", name="uq_event_session_attendance"),
        Index("ix_event_session_attendance_event_session", "event_id", "session_id"),
        Index("ix_event_session_attendance_registration", "registration_id"),
    )


class EventWaitlist(Base):
    __tablename__ = "event_waitlist"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False, index=True)
    participant_name = Column(String(255), nullable=False)
    participant_email = Column(String(255), nullable=False)
    status = Column(String(20), default="waiting", index=True)  # waiting|payment_pending|promoted|expired|left
    registration_id = Column(UUID(as_uuid=True), nullable=True)  # Linked registration if promoted
    payment_offer_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    event = relationship("Event", backref="waitlist_entries")


class EventTemplate(Base):
    __tablename__ = "event_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    enterprise_id = Column(UUID(as_uuid=True), ForeignKey("enterprises.id"), nullable=True)
    name = Column(String(255), nullable=False)
    template_data = Column(JSONB, nullable=False)
    # Historical provenance only; apply always resolves the current active form.
    configuration_id = Column(UUID(as_uuid=True), nullable=True)
    configuration_version_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class EventFeedback(Base):
    __tablename__ = "event_feedback"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False, index=True)
    participant_email = Column(String(255))
    form_id = Column(String(100))
    answers = Column(JSONB)
    rating = Column(String(10))
    comment = Column(Text)
    is_review = Column(Boolean, default=False)
    moderation_status = Column(String(20), default="pending")  # pending|approved|rejected
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    event = relationship("Event", backref="feedbacks")


class EventOrder(Base):
    __tablename__ = "event_orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False, index=True)
    participant_name = Column(String(255), nullable=False)
    participant_email = Column(String(255), nullable=False, index=True)
    ticket_type_id = Column(String(100), index=True)
    quantity = Column(String(20), default="1")
    amount = Column(String(50))  # total amount
    currency = Column(String(10), default="INR")
    payment_status = Column(String(20), default="confirmed", index=True)  # pending|confirmed|failed|refunded
    status = Column(String(20), default="confirmed", index=True)  # confirmed|cancelled|refunded|refund_requested
    payment_provider = Column(String(50), default="marketplace")  # marketplace|merchant
    refund_reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    event = relationship("Event", backref="orders")


class EventCategory(Base):
    __tablename__ = "event_categories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False, unique=True, index=True)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("event_categories.id"), nullable=True, index=True)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class EventAudit(Base):
    __tablename__ = "event_audits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False, index=True)
    changed_by = Column(String(255))
    action = Column(String(50), nullable=False)  # create|update|status_change|delete|reject|request_changes
    before = Column(JSONB)
    after = Column(JSONB)
    notes = Column(Text)  # admin reason/message for reject/request_changes
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    event = relationship("Event", backref="audit_logs")
