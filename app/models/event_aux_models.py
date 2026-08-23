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
