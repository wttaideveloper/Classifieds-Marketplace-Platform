from uuid import UUID
from datetime import datetime
from pydantic import BaseModel
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