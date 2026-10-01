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
    # Meal option ids (Event.meals) the attendee selected (Phase 2.6): a JSON list of strings in the event's option order.
    # NULL = none selected, which is every row that predates the column.
    meal_selections = Column(JSONB, nullable=True)
    # Accommodation option ids (Event.accommodation) the attendee selected (Phase 2.7): a JSON list of strings in the event's
    # option order. NULL = none selected, which is every row that predates the column.
    accommodation_selections = Column(JSONB, nullable=True)
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


class EventRegistrationOption(Base):
    """A purchased/selected Meal or Accommodation option (Phase 2.8) — an immutable, purchase-time snapshot line
    item, one row per selected option per registration. This is the authoritative financial history for
    meal/accommodation charges: changing an option's price/name/capacity later never touches an existing row
    here (name/price/currency are captured at the moment of purchase and never re-derived).

    Capacity is enforced (and released) purely by COUNTING active rows here joined to a non-cancelled
    EventRegistration — there is no separate reserved/consumed counter, so a registration or its paired order
    transitioning to cancelled/refunded releases the option's capacity automatically and idempotently (the count
    simply stops including it), with no dedicated "release" step to get wrong or double-run. See
    app.services.event_option_pricing_service.

    EventOrder has no FK to EventRegistration (see event_payments.py's docstring — pairing is normally by email
    heuristic). This table is deliberately FK'd to EventRegistration instead (created by every registration path,
    free or paid, before this table is ever written to), and also carries a nullable order_id for direct
    traceability when the purchase was paid, so a query never needs that fragile pairing.
    """

    __tablename__ = "event_registration_options"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    registration_id = Column(
        UUID(as_uuid=True), ForeignKey("event_registrations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_id = Column(UUID(as_uuid=True), ForeignKey("event_orders.id", ondelete="SET NULL"), nullable=True, index=True)
    option_type = Column(String(20), nullable=False)  # "meal" | "accommodation"
    # id inside Event.meals/accommodation options JSONB — no FK, same reasoning as EventSessionAttendance.session_id.
    option_id = Column(String(64), nullable=False)
    option_name = Column(String(255), nullable=False)  # snapshot at purchase time
    unit_price = Column(String(50), nullable=False, default="0")  # snapshot at purchase time
    currency = Column(String(10), nullable=False, default="INR")  # snapshot at purchase time
    quantity = Column(String(20), nullable=False, default="1")
    line_total = Column(String(50), nullable=False, default="0")  # snapshot: unit_price * quantity
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    event = relationship("Event")
    registration = relationship("EventRegistration", backref="options")
    order = relationship("EventOrder", backref="line_items")

    __table_args__ = (
        # Capacity-counting lookup: "how many active rows exist for this event's option X".
        Index("ix_event_registration_options_event_option", "event_id", "option_type", "option_id"),
    )


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
