from pydantic import BaseModel, EmailStr
from uuid import UUID
from datetime import datetime

class CommunicationCreate(BaseModel):
    customer_name: str
    customer_email: EmailStr
    subject: str
    message: str

class CommunicationUpdate(BaseModel):
    customer_name: str | None = None
    customer_email: EmailStr | None = None
    subject: str | None = None
    message: str | None = None
    status: str | None = None

class CommunicationResponse(BaseModel):
    id: UUID
    customer_name: str
    customer_email: str
    subject: str
    message: str
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True