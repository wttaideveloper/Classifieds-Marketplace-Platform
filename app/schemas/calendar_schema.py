from uuid import UUID
from datetime import datetime
from pydantic import BaseModel
from typing import Optional, List
from app.models.calendar_model import CalendarProvider, EventStatusEnum


class CalendarConnectRequest(BaseModel):
    merchant_id: UUID
    provider: CalendarProvider
    authorization_code: str

class CalendarConnectResponse(BaseModel):
    integration_id: UUID
    provider: CalendarProvider
    message: str

class CalendarEventCreateRequest(BaseModel):
    booking_id: UUID
    start_time: datetime
    end_time: datetime
    event_title: str

class CalendarEventCreateResponse(BaseModel):
    integration_id: UUID
    provider: CalendarProvider
    event_id: str
    meeting_link: str
    event_status: str

class CalendarStatusResponse(BaseModel):
    integration_id: UUID
    provider: CalendarProvider
    is_active: bool
    event_status: EventStatusEnum
    last_synced_at: datetime

    class Config:
        from_attributes = True

class CalendarEventUpdateRequest(BaseModel):
    event_title: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


class CalendarEventUpdateResponse(BaseModel):
    event_id: UUID
    booking_id: UUID
    event_title: str
    start_time: datetime
    end_time: datetime
    meeting_link: str | None
    event_status: EventStatusEnum

class CalendarDeleteResponse(BaseModel):
    message: str
    event_id: UUID

class CalendarAvailabilityItem(BaseModel):
    id: UUID
    booking_id: UUID
    merchant_id: UUID
    title: str
    start_time: datetime
    end_time: datetime

    class Config:
        from_attributes = True

class CalendarAvailabilityResponse(BaseModel):
    total_records: int
    events: List[CalendarAvailabilityItem]