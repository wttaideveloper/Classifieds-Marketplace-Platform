from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class NotificationTypeEnum(str, Enum):
    Booking = "Booking"
    Order = "Order"
    Review = "Review"
    System = "System"
    Payment = "Payment"


class UserRoleEnum(str, Enum):
    customer = "customer"
    merchant = "merchant"
    admin = "admin"


class CreateNotificationSchema(BaseModel):
    user_id: UUID = Field(..., description="Recipient user UUID")
    user_role: UserRoleEnum = Field(..., description="customer, merchant, or admin")
    title: str = Field(..., min_length=1, max_length=255)
    message: str = Field(..., min_length=1)
    notification_type: NotificationTypeEnum
    reference_id: Optional[UUID] = None


class NotificationResponseSchema(BaseModel):
    notification_id: str
    title: str
    message: str
    notification_type: str
    reference_id: Optional[str] = None
    is_read: bool
    created_at: datetime


class PaginatedNotificationsResponse(BaseModel):
    success: bool = True
    page: int
    size: int
    total_elements: int
    total_pages: int
    unread_count: int
    data: List[NotificationResponseSchema]


class NotificationCreateResponseSchema(BaseModel):
    success: bool = True
    message: str
    data: NotificationResponseSchema
