"""Service bookings: one customer, one Service, one time slot, one provider; plus the history of every change.

Times are stored as UTC (naive); the Service's own IANA time zone (`services.time_zone`) is what every API answer is shown in.
Payment is a status on the booking only (`payment_status`): there is no link to the payments module yet.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.database import Base


class ServiceBooking(Base):
    __tablename__ = "service_bookings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    enterprise_id = Column(UUID(as_uuid=True), ForeignKey("enterprises.id"), nullable=False, index=True)
    service_id = Column(UUID(as_uuid=True), ForeignKey("services.id"), nullable=False, index=True)
    reference = Column(String(20), unique=True, index=True)        # "SB-1A2B3C4D5E": shown to people, searchable (null only for rows the migration has not reached)

    # who booked. customer_user_id is null for a booking an admin made for someone with no account.
    customer_user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    customer_name = Column(String(255))
    customer_email = Column(String(255))
    customer_phone = Column(String(40))
    created_by = Column(UUID(as_uuid=True), nullable=True)           # the person who made the booking (the customer, or the admin acting for them)
    created_by_role = Column(String(20))                              # customer | admin | provider | super_admin

    provider_user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    provider_name = Column(String(255))

    delivery_mode = Column(String(12), nullable=False)               # in_person | virtual | hybrid
    starts_at = Column(DateTime, nullable=False, index=True)         # UTC
    ends_at = Column(DateTime, nullable=False)                       # UTC: starts_at + the service's duration
    time_zone = Column(String(64), nullable=False)                   # the service's IANA zone when it was booked
    participants = Column(Integer, nullable=False, default=1)

    # pending | confirmed | in_progress | completed | cancellation_requested | cancelled | no_show | expired
    status = Column(String(24), nullable=False, default="pending", index=True)
    status_before_request = Column(String(24))                       # restored when a cancellation request is rejected
    # not_required | unpaid | paid | refunded | partially_refunded
    payment_status = Column(String(20), nullable=False, default="not_required", index=True)
    amount = Column(Float, nullable=False, default=0)
    currency = Column(String(3))
    paid_at = Column(DateTime)
    hold_expires_at = Column(DateTime)                               # an unpaid booking holds its place until then

    notes = Column(Text)
    cancel_reason = Column(Text)
    cancelled_at = Column(DateTime)
    cancelled_by = Column(UUID(as_uuid=True))
    cancelled_by_role = Column(String(20))
    cancellation_requested_at = Column(DateTime)
    decision_reason = Column(Text)                                   # why a cancellation request was rejected (or a note on approval)
    refund_percent = Column(Integer)
    refund_amount = Column(Float)
    # independent of payment_status: null = no refund owed; pending (owed, not yet sent to the gateway) | processing | completed | failed.
    # Only a gateway confirmation may set completed: until the payment-module integration exists nothing does.
    refund_status = Column(String(20), index=True)
    checked_in_at = Column(DateTime)
    completed_at = Column(DateTime)
    idempotency_key = Column(String(100))

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    service = relationship("Service", backref="bookings")
    history = relationship("ServiceBookingEvent", backref="booking", order_by="ServiceBookingEvent.created_at", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_service_bookings_service_start", "service_id", "starts_at"),
        Index("ix_service_bookings_provider_start", "provider_user_id", "starts_at"),
        Index("ix_service_bookings_enterprise_status", "enterprise_id", "status"),
        UniqueConstraint("created_by", "idempotency_key", name="uq_service_bookings_idempotency"),
    )


class ServiceBookingEvent(Base):
    """One line of a booking's history: who did what, when, and why."""

    __tablename__ = "service_booking_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id = Column(UUID(as_uuid=True), ForeignKey("service_bookings.id", ondelete="CASCADE"), nullable=False, index=True)
    action = Column(String(40), nullable=False)                      # created | confirmed | paid | cancelled | cancellation_requested | ...
    from_status = Column(String(24))
    to_status = Column(String(24))
    actor_id = Column(UUID(as_uuid=True))
    actor_role = Column(String(20))                                  # customer | admin | provider | super_admin | system
    actor_name = Column(String(255))
    reason = Column(Text)
    detail = Column(JSONB)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
