import enum
import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import Base


class NotificationType(str, enum.Enum):
    Booking = "Booking"
    Order = "Order"
    Review = "Review"
    System = "System"
    Payment = "Payment"


class UserRole(str, enum.Enum):
    customer = "customer"
    merchant = "merchant"
    admin = "admin"


class NotificationHistoryAction(str, enum.Enum):
    Sent = "Sent"
    Read = "Read"
    Deleted = "Deleted"


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    user_role = Column(String(20), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    notification_type = Column(String(50), nullable=False, index=True)
    reference_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    is_read = Column(Boolean, default=False, nullable=False, index=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    history = relationship(
        "NotificationHistory",
        back_populates="notification",
        cascade="all, delete-orphan",
    )


class NotificationHistory(Base):
    __tablename__ = "notification_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    notification_id = Column(
        UUID(as_uuid=True),
        ForeignKey("notifications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action = Column(String(50), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    notification = relationship("Notification", back_populates="history")
