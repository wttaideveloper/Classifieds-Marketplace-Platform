from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common_schema import PaginatedResponse


TrainingStatus = str  # draft|published|unpublished|archived|cancelled


class TrainingCreate(BaseModel):
    tenant_id: UUID | None = None
    enterprise_id: UUID = Field(..., description="Enterprise ID")
    location_id: UUID | None = None
    title: str
    description: str | None = None
    category: str = Field(..., description="Category")
    subcategory: str | None = None
    tags: list[str] | None = None
    instructor_id: UUID | None = None
    requirements: str | None = None
    primary_image: str | None = None
    gallery_images: list | None = None
    promotional_video: str | None = None
    documents: list | None = None
    delivery_mode: str | None = Field("self_paced", description="self_paced|instructor_led|blended")
    course_type: str | None = Field(None, description="one_day|workshop|virtual|certification")
    duration: str | None = Field(None, description="Duration e.g. 1 day, half_day, custom, 2 weeks")
    start_date: datetime | None = None
    end_date: datetime | None = None
    enrolment_start: datetime | None = None
    enrolment_end: datetime | None = None
    time_zone: str | None = Field("Asia/Kolkata", description="Time zone")
    capacity: str | None = Field(None, description="Participant capacity")
    price: str | None = None
    currency: str | None = "INR"
    promo_price: str | None = None
    coupon_code: str | None = None
    requires_approval: bool = Field(False, description="Provider must approve enrolment")
    access_duration_days: str | None = Field(None, description="Access expiry days, e.g. 30")
    status: TrainingStatus = Field("draft")

    def to_model_data(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "enterprise_id": self.enterprise_id,
            "location_id": self.location_id,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "subcategory": self.subcategory,
            "tags": self.tags or [],
            "instructor_id": self.instructor_id,
            "requirements": self.requirements,
            "primary_image": self.primary_image,
            "gallery_images": self.gallery_images or [],
            "promotional_video": self.promotional_video,
            "documents": self.documents or [],
            "delivery_mode": self.delivery_mode,
            "course_type": self.course_type,
            "duration": self.duration,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "enrolment_start": self.enrolment_start,
            "enrolment_end": self.enrolment_end,
            "time_zone": self.time_zone,
            "capacity": self.capacity,
            "price": self.price,
            "currency": self.currency,
            "promo_price": self.promo_price,
            "coupon_code": self.coupon_code,
            "requires_approval": self.requires_approval,
            "access_duration_days": self.access_duration_days,
            "status": self.status,
        }


class TrainingUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    category: str | None = None
    subcategory: str | None = None
    tags: list | None = None
    instructor_id: UUID | None = None
    requirements: str | None = None
    primary_image: str | None = None
    gallery_images: list | None = None
    promotional_video: str | None = None
    documents: list | None = None
    delivery_mode: str | None = None
    course_type: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    enrolment_start: datetime | None = None
    enrolment_end: datetime | None = None
    time_zone: str | None = None
    capacity: str | None = None
    price: str | None = None
    currency: str | None = None
    promo_price: str | None = None
    coupon_code: str | None = None
    requires_approval: bool | None = None
    access_duration_days: str | None = None
    status: TrainingStatus | None = None

    def to_model_data(self) -> dict:
        return self.model_dump(exclude_unset=True)


class TrainingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID | None = None
    enterprise_id: UUID
    location_id: UUID | None = None
    title: str
    description: str | None = None
    category: str
    subcategory: str | None = None
    tags: list | None = None
    instructor_id: UUID | None = None
    delivery_mode: str | None = None
    course_type: str | None = None
    capacity: str | None = None
    price: str | None = None
    currency: str | None = None
    status: str
    is_deleted: bool | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    sections: list | None = None
    assessments: list | None = None


class TrainingListItemResponse(TrainingResponse):
    pass


class TrainingDetailResponse(TrainingResponse):
    enterprise_name: str | None = None
    model_config = ConfigDict(from_attributes=True)


class TrainingPaginatedResponse(PaginatedResponse[TrainingListItemResponse]):
    pass


class TrainingStatusUpdate(BaseModel):
    status: str
    reason: str | None = None


class SectionCreate(BaseModel):
    model_config = ConfigDict(extra="allow")
    title: str = Field(..., description="Section/Module title")
    type: str = Field("section", description="section|module")
    order: int | None = 0
    instructor_id: UUID | None = Field(None, description="Section instructor allocation")
    schedule: dict | None = Field(None, description="Schedule/agenda for section")

class LessonCreate(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str = Field("text", description="text|video|audio|webpage|pdf|live|presentation|worksheet|document")
    title: str
    content_url: str | None = None
    topics: list | None = Field(None, description="Topics within lesson: [{title, content_url}]")
    duration: int | None = None
    is_preview: bool | None = Field(False, description="Preview allowed without enrolment")
    is_draft: bool | None = Field(False, description="Draft mode — hidden until published")
    is_mandatory: bool | None = Field(False, description="Mandatory lesson")
    completion_rule: str | None = Field(None, description="Completion rule, e.g. mandatory")
    prerequisites: list | None = Field(None, description="Lesson IDs that must be completed first — sequential learning")
    release_rule: dict | None = Field(None, description="Release: {mode: 'date'|'enrolment_day'|'previous_lesson', date: '2026-01-01', days: 2, lesson_id: '...'}")
    instructor_id: UUID | None = Field(None, description="Lesson instructor allocation")


class AssessmentQuestionCreate(BaseModel):
    model_config = ConfigDict(extra="allow")
    question_text: str = Field(..., description="The question text")
    question_type: str = Field("mcq", description="mcq|multiple_select|true_false|short_answer|essay")
    options: list[str] | None = Field(None, description="Multiple choice options (for mcq/multiple_select)")
    correct_answer: str | None = Field(None, description="Correct answer (for mcq/true_false) or comma-separated for multiple_select")
    points: int = Field(1, description="Points for correct answer")
    explanation: str | None = Field(None, description="Answer explanation")
    reusable: bool | None = Field(False, description="Store in question bank for reuse")


class AssessmentSubmitCreate(BaseModel):
    answers: list[dict] = Field(..., description="List of answer selections, each with question_id and answer")


class AssessmentSubmitResponse(BaseModel):
    score: int
    passed: bool
    total_points: int
    feedback: str | None = None


class AssignmentCreate(BaseModel):
    model_config = ConfigDict(extra="allow")
    title: str = Field(..., description="Assignment title")
    type: str = Field("assignment", description="assignment|task|practical")
    instructions: str | None = Field(None, description="Assignment instructions")
    due_date: datetime | None = None
    max_score: int | None = Field(None, description="Maximum possible score")
    accepted_file_types: list[str] | None = Field(
        None, description="Allowed file extensions, e.g. ['.pdf', '.docx']"
    )
    allow_late_submissions: bool = Field(
        False, description="Allow submissions after due date"
    )


class AssignmentSubmitCreate(BaseModel):
    file_url: str | None = Field(None, description="URL to uploaded file")
    submission_text: str | None = Field(None, description="Text submission content")


class AssignmentSubmitResponse(BaseModel):
    id: str
    submitted_at: str
    grade: int | None = None
    feedback: str | None = None


class TrainingProgressSection(BaseModel):
    section_id: str
    section_title: str
    lessons_done: int
    total_lessons: int


class TrainingProgressLesson(BaseModel):
    lesson_id: str
    lesson_title: str
    is_completed: bool


class TrainingProgressResponse(BaseModel):
    overall_percent: float
    sections_done: int
    total_sections: int
    lessons_done: int
    total_lessons: int
    certificate_url: str | None = None
    sections_detail: list[TrainingProgressSection]
    lessons_detail: list[TrainingProgressLesson]
    expired: bool = False
    status: str = Field("active", description="active|expired")
    access_expires_at: str | None = None


class TrainingLiveSessionCreate(BaseModel):
    title: str = Field(..., description="Session title")
    description: str | None = None
    scheduled_at: datetime = Field(..., description="Scheduled date/time")
    duration_minutes: int = Field(..., description="Duration in minutes")
    meeting_link: str = Field(..., description="Meeting link (Zoom/Teams URL)")
    meeting_provider: str = Field("zoom", description="zoom|teams|meet|other")


class TrainingLiveSessionResponse(BaseModel):
    id: str
    title: str
    scheduled_at: str
    duration_minutes: int
    meeting_link: str
    meeting_provider: str
    status: str = "scheduled"
    recording_url: str | None = None


class AnnouncementCreate(BaseModel):
    title: str | None = None
    message: str = Field(..., description="Announcement message")
    channel: str = Field("in_app", description="in_app|email|sms|both")


class AnnouncementResponse(BaseModel):
    id: str
    training_id: UUID
    title: str | None = None
    message: str
    sent_at: str
    channel: str


class TrainingCheckoutRequest(BaseModel):
    participant_name: str
    participant_email: str
    quantity: int = Field(1, ge=1)
    coupon_code: str | None = None
    payment_provider: str | None = Field("marketplace", description="marketplace|merchant")


class TrainingOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    training_id: UUID
    participant_name: str
    participant_email: str
    quantity: str
    amount: str | None = None
    currency: str | None = None
    payment_status: str
    status: str
    created_at: datetime | None = None


class EnrolApprovalRequest(BaseModel):
    action: str = Field(..., description="approve|reject")
    reason: str | None = None


# ---- Training Order Status & Refund ----

class TrainingOrderStatusUpdate(BaseModel):
    status: str = Field(..., description="New status: confirmed|cancelled|completed")
    reason: str | None = Field(None, description="Reason for status change")

class TrainingRefundRequest(BaseModel):
    reason: str | None = Field(None, description="Reason for refund")
    amount: str | None = Field(None, description="Partial amount if partial refund")

class TrainingRefundApproveRequest(BaseModel):
    action: str = Field(..., description="approve|reject")
    reason: str | None = Field(None, description="Reason for approval/rejection")
