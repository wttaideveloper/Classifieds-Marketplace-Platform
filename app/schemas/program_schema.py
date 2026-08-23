from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from app.schemas.common_schema import PaginatedResponse

class ProgramCreate(BaseModel):
    tenant_id: UUID | None = None
    enterprise_id: UUID = Field(..., description="Enterprise ID")
    location_id: UUID | None = None
    title: str
    description: str | None = None
    category: str = Field(..., description="Category")
    provider_id: UUID | None = None
    duration_weeks: str | None = None
    eligibility: dict | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    enrolment_start: datetime | None = None
    enrolment_end: datetime | None = None
    enrol_type: str | None = "fixed"
    delivery_mode: str | None = "offline"
    price: str | None = None
    currency: str | None = "INR"
    capacity: str | None = None
    status: str = Field("draft")
    def to_model_data(self): return {"tenant_id": self.tenant_id, "enterprise_id": self.enterprise_id, "location_id": self.location_id, "title": self.title, "description": self.description, "category": self.category, "provider_id": self.provider_id, "duration_weeks": self.duration_weeks, "eligibility": self.eligibility or {}, "start_date": self.start_date, "end_date": self.end_date, "enrolment_start": self.enrolment_start, "enrolment_end": self.enrolment_end, "enrol_type": self.enrol_type, "delivery_mode": self.delivery_mode, "price": self.price, "currency": self.currency, "capacity": self.capacity, "status": self.status}

class ProgramUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    category: str | None = None
    provider_id: UUID | None = None
    duration_weeks: str | None = None
    eligibility: dict | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    enrolment_start: datetime | None = None
    enrolment_end: datetime | None = None
    enrol_type: str | None = None
    delivery_mode: str | None = None
    price: str | None = None
    currency: str | None = None
    capacity: str | None = None
    status: str | None = None
    def to_model_data(self): return self.model_dump(exclude_unset=True)

class ProgramResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    enterprise_id: UUID
    title: str
    description: str | None = None
    category: str
    delivery_mode: str | None = None
    price: str | None = None
    status: str
    is_deleted: bool | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    phases: list | None = None

class ProgramListItemResponse(ProgramResponse): pass
class ProgramDetailResponse(ProgramResponse):
    enterprise_name: str | None = None
    model_config = ConfigDict(from_attributes=True)
class ProgramPaginatedResponse(PaginatedResponse[ProgramListItemResponse]): pass
class ProgramStatusUpdate(BaseModel):
    status: str; reason: str | None = None
class PhaseCreate(BaseModel):
    title: str; type: str = "phase"; order: int | None = 0; prerequisites: list | None = None; completion_rule: str | None = None
class ActivityCreate(BaseModel):
    type: str; title: str; content_url: str | None = None; release: dict | None = None
