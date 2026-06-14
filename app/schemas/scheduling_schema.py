from pydantic import BaseModel, EmailStr
from datetime import datetime
from uuid import UUID
from typing import Optional


class AppointmentCreate(BaseModel):
    title: str
    description: Optional[str] = None
    customer_name: str
    customer_email: EmailStr
    start_time: datetime
    end_time: datetime


class AppointmentUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    customer_name: Optional[str] = None
    customer_email: Optional[EmailStr] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    status: Optional[str] = None


class AppointmentResponse(BaseModel):
    id: UUID
    title: str
    description: Optional[str]
    customer_name: str
    customer_email: str
    start_time: datetime
    end_time: datetime
    status: str

    class Config:
        from_attributes = True