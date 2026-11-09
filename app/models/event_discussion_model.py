"""Event Discussions: messages of one event's discussion, the moderation history, who has joined, and the nudge bookkeeping.

A message is `pending_moderation` until the AI has judged it, then `published` (safe), `pending_review` (flagged: waits for an Enterprise
Admin), or `blocked` (extreme). An Enterprise Admin's decision makes a flagged message `published` or `deleted`. Only `published` messages
are visible to other people.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.database import Base


class EventDiscussionMessage(Base):
    __tablename__ = "event_discussion_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)             # the event's owning tenant, for the Enterprise Admin's lists
    parent_id = Column(UUID(as_uuid=True), ForeignKey("event_discussion_messages.id"), nullable=True, index=True)   # the message replied to
    root_id = Column(UUID(as_uuid=True), nullable=True, index=True)               # the top message of the thread (replies are one level deep)
    author_id = Column(String(64), nullable=False, index=True)
    author_name = Column(String(255))
    author_role = Column(String(20), nullable=False)                              # participant | enterprise_admin
    body = Column(Text, nullable=False)
    is_announcement = Column(Boolean, nullable=False, default=False)
    status = Column(String(20), nullable=False, default="pending_moderation", index=True)  # pending_moderation | published | pending_review | blocked | deleted
    is_appeal = Column(Boolean, nullable=False, default=False)
    moderation_label = Column(String(10))                                          # safe | flagged | extreme
    moderation_reason = Column(String(500))
    moderation_provider = Column(String(30))
    moderation_attempts = Column(Integer, nullable=False, default=0)
    decided_by = Column(String(64))
    decided_by_name = Column(String(255))
    decided_at = Column(DateTime)
    decision_note = Column(String(500))
    recipient_id = Column(String(64), index=True)                                   # for a reply: the author of the message replied to
    recipient_read_at = Column(DateTime)
    nudged_at = Column(DateTime)
    published_at = Column(DateTime, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (Index("ix_event_discussion_event_status", "event_id", "status", "created_at"),)


class EventDiscussionModeration(Base):
    """Every step a message went through, kept forever: who or what did it, the result and why."""
    __tablename__ = "event_discussion_moderation"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    message_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    action = Column(String(30), nullable=False)             # submitted | auto_published | flagged | blocked | ai_unavailable | approved | deleted | edited_resubmitted | appealed
    from_status = Column(String(20))
    to_status = Column(String(20))
    label = Column(String(10))
    reason = Column(String(500))
    actor_id = Column(String(64))                           # null for the AI / the system
    actor_name = Column(String(255))
    actor_role = Column(String(20))                         # participant | enterprise_admin | system
    detail = Column(JSONB)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class EventDiscussionMember(Base):
    """A person known to the discussion: learned when they first open it, so announcements and nudges can reach their user id
    (registrations only hold an e-mail address)."""
    __tablename__ = "event_discussion_members"
    __table_args__ = (UniqueConstraint("event_id", "user_id", name="uq_event_discussion_member"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    name = Column(String(255))
    email = Column(String(255))
    role = Column(String(20), nullable=False)               # participant | enterprise_admin
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class EventDiscussionNudge(Base):
    """When the Enterprise Admins of an event were last reminded about flagged messages, and how many times."""
    __tablename__ = "event_discussion_nudges"

    event_id = Column(UUID(as_uuid=True), primary_key=True)
    last_sent_at = Column(DateTime, nullable=False)
    sent_count = Column(Integer, nullable=False, default=0)
