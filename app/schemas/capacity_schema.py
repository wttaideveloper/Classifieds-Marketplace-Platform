from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID
from datetime import datetime
from app.models.capacity_model import CapacityStatus

class CapacityCreate(BaseModel):

    listing_id: UUID
    total_capacity: int = Field(
        ...,
        gt=0
    )
    waitlist_enabled: Optional[bool] = False


class CapacityUpdate(BaseModel):

    total_capacity: int = Field(
        ...,
        gt=0
    )
    waitlist_enabled: Optional[bool] = False
    remarks: Optional[str] = None

class CapacityResponse(BaseModel):

    id: UUID
    listing_id: UUID
    total_capacity: int
    booked_capacity: int
    available_capacity: int
    waitlist_enabled: bool
    capacity_status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class CapacityHistoryResponse(BaseModel):

    id: UUID
    listing_id: UUID
    old_capacity: int
    new_capacity: int
    updated_by: Optional[UUID]
    remarks: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True

class CapacityUpdateResponse(BaseModel):

    listing_id: UUID
    total_capacity: int
    booked_capacity: int
    available_capacity: int
    capacity_status: str

    class Config:
        from_attributes = True

class CapacityAvailabilityResponse(BaseModel):

    total_capacity: int
    available_capacity: int
    booking_allowed: bool
    capacity_status: str

    class Config:
        from_attributes = True

class CapacityStatusUpdate(BaseModel):

    capacity_status: CapacityStatus