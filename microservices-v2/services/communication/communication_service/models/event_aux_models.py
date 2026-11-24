"""v2 communication: READ-ONLY replica of EventOrder, EventRegistration (kept current by mskit.replication).
Foreign keys to tables this service does not hold were dropped; columns, types and indexes are the monolith's."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from communication_service.db.database import Base


class EventRegistration(Base):
    __tablename__ = "event_registrations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False, index=True)
    participant_name = Column(String(255), nullable=False)
    participant_email = Column(String(255), nullable=False, index=True)
    custom_fields = Column(JSONB, default=dict)
    # The registrant's platform user id (what Event Group Chat invites). NULL = not known yet: it is saved when the registrant
    # registers themselves (verified identity), then recovered by email (app/services/event_registration_identity.py). Walk-ins
    # and group members registered by someone else stay NULL. Never taken from request input.
    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
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
    # Meal option ids (Event.meals) the attendee selected (Phase 2.6): a JSON list of strings in the event's option order.
    # NULL = none selected, which is every row that predates the column.
    meal_selections = Column(JSONB, nullable=True)
    # Accommodation option ids (Event.accommodation) the attendee selected (Phase 2.7): a JSON list of strings in the event's
    # option order. NULL = none selected, which is every row that predates the column.
    accommodation_selections = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    event = relationship("Event", backref="registrations")










class EventOrder(Base):
    __tablename__ = "event_orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False, index=True)
    participant_name = Column(String(255), nullable=False)
    participant_email = Column(String(255), nullable=False, index=True)
    ticket_type_id = Column(String(100), index=True)
    quantity = Column(String(20), default="1")
    amount = Column(String(50))  # total amount (Phase 2.8: ticket + meal + accommodation subtotals combined)
    currency = Column(String(10), default="INR")
    payment_status = Column(String(20), default="confirmed", index=True)  # pending|confirmed|failed|refunded
    status = Column(String(20), default="confirmed", index=True)  # confirmed|cancelled|refunded|refund_requested
    payment_provider = Column(String(50), default="marketplace")  # marketplace|merchant
    refund_reason = Column(Text)
    # Phase 2.8: component breakdown of `amount`, for audit/quote display. NULL on every order created before
    # this column existed (backfilled to ticket_subtotal=amount, meal/accommodation_subtotal=0 by the migration
    # for reporting consistency — `amount` itself is untouched and remains authoritative either way).
    ticket_subtotal = Column(String(50), nullable=True)
    meal_subtotal = Column(String(50), nullable=True)
    accommodation_subtotal = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    event = relationship("Event", backref="orders")








