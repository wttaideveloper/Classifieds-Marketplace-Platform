import enum
import uuid

from sqlalchemy import Boolean, Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.database import Base


class DeviceType(str, enum.Enum):
    Android = "Android"
    iOS = "iOS"
    Web = "Web"


class PushNotificationType(str, enum.Enum):
    Booking = "Booking"
    Order = "Order"
    System = "System"


class DeliveryStatus(str, enum.Enum):
    Pending = "Pending"
    Sent = "Sent"
    Failed = "Failed"


class DeviceToken(Base):
    __tablename__ = "device_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    device_token = Column(String(512), nullable=False, index=True)
    device_type = Column(String(20), nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PushNotification(Base):
    __tablename__ = "push_notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    notification_type = Column(String(50), nullable=False, index=True)
    reference_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    delivery_status = Column(String(20), nullable=False, default=DeliveryStatus.Pending.value)
    is_read = Column(Boolean, default=False, nullable=False, index=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
