from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID
from datetime import datetime
from app.models.meeting_model import MeetingProviderEnum, MeetingStatusEnum


class MeetingConnectRequest(BaseModel):
    merchant_id: UUID
    provider: MeetingProviderEnum
    authorization_code: Optional[str] = None


class MeetingConnectResponse(BaseModel):
    id: UUID
    merchant_id: UUID
    provider: MeetingProviderEnum
    provider_account_id: str | None
    is_active: bool

    class Config:
        from_attributes = True

class CreateMeetingRequest(BaseModel):
    booking_id: UUID
    meeting_title: str = Field(..., min_length=3)
    start_time: datetime
    end_time: datetime
    provider: MeetingProviderEnum


class CreateMeetingResponse(BaseModel):
    meeting_id: UUID
    meeting_link: str
    provider: MeetingProviderEnum
    meeting_status: MeetingStatusEnum
    start_time: datetime

    class Config:
        from_attributes = True

class UpdateMeetingRequest(BaseModel):
    meeting_title: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


class MeetingResponse(BaseModel):
    id: UUID
    booking_id: UUID
    merchant_id: UUID
    provider: MeetingProviderEnum
    meeting_title: str
    meeting_link: str
    start_time: datetime
    end_time: datetime
    meeting_status: MeetingStatusEnum

    class Config:
        from_attributes = True