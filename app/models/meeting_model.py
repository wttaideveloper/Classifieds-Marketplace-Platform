"""Virtual meeting providers (Zoom, Google Meet, Microsoft Teams): platform switches, merchant connections, links.

The platform only creates and shares meeting links. Nothing here runs a call, records it or tracks who joined.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.database import Base


class MeetingProviderSetting(Base):
    """The Super Admin's switch for one provider across the whole platform. A provider with no row is on."""

    __tablename__ = "meeting_provider_settings"

    provider = Column(String(20), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=True)
    updated_by = Column(UUID(as_uuid=True), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class MeetingConnection(Base):
    """A business's connected account at one provider. Tokens are stored encrypted. One account per provider per business."""

    __tablename__ = "meeting_connections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    provider = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="connected")           # connected | reconnect_needed
    account_id = Column(String(255))
    account_email = Column(String(255))
    account_name = Column(String(255))
    access_token = Column(Text)                                                # encrypted
    refresh_token = Column(Text)                                               # encrypted
    token_expires_at = Column(DateTime)
    scope = Column(Text)
    connected_by = Column(UUID(as_uuid=True), nullable=True)
    last_error = Column(String(255))
    connected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("tenant_id", "provider", name="uq_meeting_connection_tenant_provider"),)


class MeetingMerchantSettings(Base):
    __tablename__ = "meeting_merchant_settings"

    tenant_id = Column(UUID(as_uuid=True), primary_key=True)
    default_provider = Column(String(20))
    auto_generate = Column(Boolean, nullable=False, default=True)
    updated_by = Column(UUID(as_uuid=True), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class MeetingLink(Base):
    """The meeting settings and state of one listing: an event, one session of an event, or one live session of a training. A pasted link takes priority over a generated one."""

    __tablename__ = "meeting_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    resource_type = Column(String(30), nullable=False)                         # event | event_session | training_session
    resource_id = Column(UUID(as_uuid=True), nullable=False)                   # the event, or the training's live session
    scope = Column(String(100), nullable=False, default="")                    # the session id for one session of an event; "" otherwise
    provider = Column(String(20))                                              # the provider chosen for this listing
    auto_generate = Column(Boolean, nullable=True)                              # null = follow the business default
    manual_link = Column(Text)
    status = Column(String(12), nullable=False, default="none")                # none | created | failed
    external_id = Column(String(255))
    join_url = Column(Text)
    passcode = Column(String(100))
    dial_in = Column(JSONB)
    connection_id = Column(UUID(as_uuid=True), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(String(500))
    generated_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("resource_type", "resource_id", "scope", name="uq_meeting_link_resource"),
        Index("ix_meeting_links_status", "status", "attempts"),
    )


class MeetingAudit(Base):
    """Who connected, disconnected or changed a link, and when. Never edited. Never holds a token."""

    __tablename__ = "meeting_audit"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    resource_type = Column(String(30))
    resource_id = Column(UUID(as_uuid=True))
    provider = Column(String(20))
    action = Column(String(40), nullable=False)
    actor_id = Column(UUID(as_uuid=True), nullable=True)
    details = Column(JSONB)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
