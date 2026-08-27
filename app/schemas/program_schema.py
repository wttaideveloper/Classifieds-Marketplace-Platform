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
    model_config = ConfigDict(extra="allow")
    title: str; type: str = "phase"; phase_type: str | None = Field(None, description="phase|stage|week|day|milestone"); order: int | None = 0; prerequisites: list | None = None; completion_rule: str | None = None; release_schedule: dict | None = Field(None, description="daily|weekly|milestone release: {mode:'daily', day:1}"); goals: dict | None = None; baseline: dict | None = None; expected_outcomes: dict | None = None; instructors: list | None = None
class ActivityCreate(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str = Field(..., description="lesson|appointment|task|assessment|live_session|document|video|webpage"); title: str; content_url: str | None = None; release: dict | None = None; session_type: str | None = Field(None, description="individual|group"); resource_url: str | None = None; document_url: str | None = None; video_url: str | None = None; webpage_url: str | None = None; prerequisites: list | None = None


class EnrolmentCreate(BaseModel):
    participant_name: str = Field(..., description="Participant name")
    participant_email: str = Field(..., description="Participant email")
    group_enrol: bool = Field(False, description="Whether this is a group enrolment")
    goals: dict | None = Field(None, description="Participant goals")
    baseline: dict | None = Field(None, description="Baseline information")
    expected_outcomes: dict | None = Field(None, description="Expected outcomes")


class EnrolmentResponse(BaseModel):
    id: str
    program_id: str
    participant_name: str
    participant_email: str
    status: str
    created_at: str


class CheckinCreate(BaseModel):
    participant_email: str = Field(..., description="Participant email")
    phase_id: str | None = Field(None, description="Phase ID")
    notes: str | None = None


class CheckinResponse(BaseModel):
    id: str
    program_id: str
    participant_email: str
    phase_id: str | None = None
    notes: str | None = None
    created_at: str


class SurveyCreate(BaseModel):
    title: str = Field(..., description="Survey title")
    description: str | None = None
    questions: list[dict] = Field(
        ..., description="List of survey questions with text and type"
    )


class SurveyResponse(BaseModel):
    id: str
    program_id: str
    title: str
    description: str | None = None
    answers: dict | None = None
    created_at: str


class ReviewCreate(BaseModel):
    rating: int = Field(..., ge=1, le=5, description="Rating from 1 to 5")
    comment: str | None = None
    participant_email: str = Field(..., description="Participant email")


class ReviewResponse(BaseModel):
    id: str
    program_id: str
    rating: int
    comment: str | None = None
    participant_email: str
    created_at: str


class ProgressStage(BaseModel):
    stage_number: int
    stage_name: str
    completion_percent: float
    milestones_achieved: list[str]


class MilestoneProgress(BaseModel):
    milestone_id: str
    milestone_name: str
    achieved: bool
    achieved_at: str | None = None


class ProgressResponse(BaseModel):
    overall: float
    stage: list[ProgressStage]
    milestone: list[MilestoneProgress]
    milestones_achieved: bool


class DashboardParticipantResponse(BaseModel):
    program_id: str
    enrolment_status: str
    phases: list[dict]
    recent_activities: list[dict]
    overall_progress: float
    certificate_url: str | None = None


class DashboardProviderResponse(BaseModel):
    program_id: str
    total_enrolments: int
    by_status: dict[str, int]
    by_phase: dict[str, dict]
    capacity_utilization: float
    recent_enrolments: list[dict]


class SummaryResponse(BaseModel):
    total_programs: int
    by_status: dict[str, int]
    by_category: dict[str, int]
    by_delivery_mode: dict[str, int]
    total_enrolments: int
    total_checkins: int
