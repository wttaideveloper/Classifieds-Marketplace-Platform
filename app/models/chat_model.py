import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.database import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    status = Column(String(20), default="open", nullable=False, index=True)
    conversation_type = Column(String(20), default="standard", nullable=False)
    is_read_only = Column(Boolean, default=False, nullable=False)
    subject = Column(String(255), nullable=True)
    context_type = Column(String(50), nullable=True)
    context_id = Column(UUID(as_uuid=True), nullable=True)
    assigned_provider_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    expires_at = Column(DateTime, nullable=True)
    archived_at = Column(DateTime, nullable=True)
    last_message_at = Column(DateTime, nullable=True)
    last_message_preview = Column(String(500), nullable=True)
    created_by = Column(UUID(as_uuid=True), nullable=False, index=True)
    is_deleted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    participants = relationship(
        "ConversationParticipant",
        back_populates="conversation",
        cascade="all, delete-orphan",
    )
    messages = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )

    __table_args__ = (
        Index("ix_conversations_status_type", "status", "conversation_type"),
    )


class ConversationParticipant(Base):
    __tablename__ = "conversation_participants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    role = Column(String(20), default="customer", nullable=False)
    last_read_at = Column(DateTime, nullable=True)
    joined_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    conversation = relationship("Conversation", back_populates="participants")

    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "user_id",
            name="uq_conversation_participants_conversation_user",
        ),
    )


class Message(Base):
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    content = Column(Text, nullable=True)
    message_type = Column(String(20), default="text", nullable=False)
    attachment_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    is_deleted = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime, nullable=True)
    is_edited = Column(Boolean, default=False, nullable=False)
    edited_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    conversation = relationship("Conversation", back_populates="messages")
    read_receipts = relationship(
        "MessageReadReceipt",
        back_populates="message",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )


class MessageReadReceipt(Base):
    __tablename__ = "message_read_receipts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id = Column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    read_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    message = relationship("Message", back_populates="read_receipts")

    __table_args__ = (
        UniqueConstraint(
            "message_id",
            "user_id",
            name="uq_message_read_receipts_message_user",
        ),
    )


class ChatAttachment(Base):
    __tablename__ = "chat_attachments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    message_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    uploaded_by = Column(UUID(as_uuid=True), nullable=False, index=True)
    file_name = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    mime_type = Column(String(100), nullable=False)
    file_size = Column(Integer, nullable=False)
    attachment_type = Column(String(20), nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)
    transcript = Column(Text, nullable=True)
    transcribed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class DeviceToken(Base):
    __tablename__ = "device_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    token = Column(String(500), nullable=False, unique=True)
    platform = Column(String(20), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    push_enabled = Column(Boolean, default=True, nullable=False)
    email_enabled = Column(Boolean, default=True, nullable=False)
    in_app_enabled = Column(Boolean, default=True, nullable=False)
    quiet_hours_start = Column(String(5), nullable=True)
    quiet_hours_end = Column(String(5), nullable=True)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class ChatNotification(Base):
    __tablename__ = "chat_notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    notification_type = Column(String(50), nullable=False)
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    data = Column(JSONB, default=dict)
    is_read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class ChatSubscription(Base):
    __tablename__ = "chat_subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    plan_type = Column(String(20), default="free", nullable=False)
    monthly_message_limit = Column(Integer, default=10, nullable=False)
    messages_used_this_month = Column(Integer, default=0, nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class UserPresence(Base):
    __tablename__ = "user_presence"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    status = Column(String(20), default="offline", nullable=False)
    last_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class TypingIndicator(Base):
    __tablename__ = "typing_indicators"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    is_typing = Column(Boolean, default=False, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "user_id",
            name="uq_typing_indicators_conversation_user",
        ),
    )


class ProviderAssignment(Base):
    __tablename__ = "provider_assignments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    assigned_by = Column(UUID(as_uuid=True), nullable=False)
    status = Column(String(20), default="active", nullable=False)
    assigned_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    reassigned_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_provider_assignments_conversation_status", "conversation_id", "status"),
    )
