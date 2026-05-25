from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DeviceTypeEnum(str, Enum):
    Android = "Android"
    iOS = "iOS"
    Web = "Web"


class PushNotificationTypeEnum(str, Enum):
    Booking = "Booking"
    Order = "Order"
    System = "System"


class DeliveryStatusEnum(str, Enum):
    Pending = "Pending"
    Sent = "Sent"
    Failed = "Failed"


class RegisterDeviceRequest(BaseModel):
    device_token: str = Field(..., min_length=1, max_length=512)
    device_type: DeviceTypeEnum


class DeviceTokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    device_token: str
    device_type: DeviceTypeEnum
    is_active: bool
    created_at: datetime
    updated_at: datetime


class RegisterDeviceResponse(BaseModel):
    success: bool = True
    message: str
    data: DeviceTokenResponse


class SendPushNotificationRequest(BaseModel):
    user_id: UUID
    title: str = Field(..., min_length=1, max_length=255)
    message: str = Field(..., min_length=1)
    notification_type: PushNotificationTypeEnum
    reference_id: Optional[UUID] = None


class SendPushNotificationResponseData(BaseModel):
    notification_id: UUID
    delivery_status: DeliveryStatusEnum
    sent_at: Optional[datetime] = None


class SendPushNotificationResponse(BaseModel):
    success: bool = True
    message: str
    data: SendPushNotificationResponseData


class PushNotificationResponse(BaseModel):
    notification_id: UUID
    user_id: UUID
    title: str
    message: str
    notification_type: PushNotificationTypeEnum
    reference_id: Optional[UUID] = None
    delivery_status: DeliveryStatusEnum
    is_read: bool
    sent_at: Optional[datetime] = None
    created_at: datetime


class PushNotificationListResponse(BaseModel):
    success: bool = True
    page: int
    size: int
    total_elements: int
    total_pages: int
    unread_count: int
    data: List[PushNotificationResponse]
