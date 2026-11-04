"""Tables. Users are plain ids (strings): the service does not own users, it only notifies them."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.utcnow()


class EventType(Base):
    """What a kind of event looks like when it reaches a person: wording, channels, priority. Edited through the admin API."""
    __tablename__ = "event_types"

    event: Mapped[str] = mapped_column(String(100), primary_key=True)          # for example payment.succeeded
    category: Mapped[str] = mapped_column(String(50), default="general")       # what a person switches on or off
    title: Mapped[str] = mapped_column(String(255))                              # "Payment of {amount} received"
    body: Mapped[str] = mapped_column(Text, default="")
    channels: Mapped[list] = mapped_column(JSON, default=lambda: ["in_app", "push"])
    priority: Mapped[str] = mapped_column(String(10), default="normal")          # normal | high | critical (critical ignores preferences)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Notification(Base):
    """One notification for one person. This is what the app's inbox shows."""
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("source", "idempotency_key", "user_id", name="uq_notification_idem"),
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    source: Mapped[str] = mapped_column(String(50))                              # the producer that sent it
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    event: Mapped[str] = mapped_column(String(100), index=True)
    category: Mapped[str] = mapped_column(String(50), default="general")
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    priority: Mapped[str] = mapped_column(String(10), default="normal")
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Delivery(Base):
    """One attempt series to reach a person over one channel. The worker sends these and retries failures."""
    __tablename__ = "deliveries"
    __table_args__ = (Index("ix_deliveries_due", "status", "next_attempt_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    notification_id: Mapped[str] = mapped_column(ForeignKey("notifications.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20))                            # push | email
    target: Mapped[str | None] = mapped_column(String(255), nullable=True)      # an email address; push uses the user's devices
    status: Mapped[str] = mapped_column(String(15), default="pending")          # pending | sent | failed | skipped
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("token", name="uq_device_token"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    token: Mapped[str] = mapped_column(String(512))
    platform: Mapped[str] = mapped_column(String(10), default="android")        # android | ios | web
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Preference(Base):
    """A person's choice for one category on one channel. No row means allowed."""
    __tablename__ = "preferences"
    __table_args__ = (UniqueConstraint("user_id", "category", "channel", name="uq_preference"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(50))
    channel: Mapped[str] = mapped_column(String(20))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class NudgeRule(Base):
    """When `trigger_event` happens and `cancel_event` does not follow within `delay_minutes`, send `nudge_event`."""
    __tablename__ = "nudge_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    trigger_event: Mapped[str] = mapped_column(String(100), index=True)
    cancel_event: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    nudge_event: Mapped[str] = mapped_column(String(100))
    delay_minutes: Mapped[int] = mapped_column(Integer)
    max_nudges: Mapped[int] = mapped_column(Integer, default=1)                  # how many reminders in total
    repeat_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)   # gap between reminders when max_nudges > 1
    subject_field: Mapped[str] = mapped_column(String(50), default="subject_id")  # the key in `data` that says what the nudge is about
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class NudgeJob(Base):
    __tablename__ = "nudge_jobs"
    __table_args__ = (Index("ix_nudge_due", "status", "due_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    rule_id: Mapped[str] = mapped_column(ForeignKey("nudge_rules.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject: Mapped[str] = mapped_column(String(120))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(12), default="pending")           # pending | done | cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
