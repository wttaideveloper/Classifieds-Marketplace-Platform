import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, Text
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
    status = Column(String(20), default="confirmed", index=True)  # confirmed|cancelled|attended|no_show
    qr_code = Column(String(255))
    checked_in_at = Column(DateTime, nullable=True)
    checked_in_by = Column(UUID(as_uuid=True), nullable=True)
    checked_out_at = Column(DateTime, nullable=True)
    session_id = Column(String(100), nullable=True)  # For per-session attendance tracking
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    event = relationship("Event", backref="registrations")


class EventWaitlist(Base):
    __tablename__ = "event_waitlist"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False, index=True)
    participant_name = Column(String(255), nullable=False)
    participant_email = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    event = relationship("Event", backref="waitlist_entries")


class EventTemplate(Base):
    __tablename__ = "event_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    enterprise_id = Column(UUID(as_uuid=True), ForeignKey("enterprises.id"), nullable=True)
    name = Column(String(255), nullable=False)
    template_data = Column(JSONB, nullable=False)
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
    action = Column(String(50), nullable=False)  # create|update|status_change|delete
    before = Column(JSONB)
    after = Column(JSONB)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    event = relationship("Event", backref="audit_logs")
