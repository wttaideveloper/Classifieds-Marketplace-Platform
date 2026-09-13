from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common_schema import PaginatedResponse


TrainingStatus = str  # draft|published|unpublished|archived|cancelled


class TrainingNoteDocument(BaseModel):
    """An instructor-uploaded notes file — the file itself is uploaded to
    storage by the client; this just records where it lives."""
    title: str | None = Field(None, description="Display name, e.g. 'Week 1 Handout'")
    url: str = Field(..., description="URL of the already-uploaded notes file (e.g. PDF)")


class TrainingRecurrence(BaseModel):
    frequency: str = Field(..., description="daily|weekly|monthly")
    interval: int = Field(1, description="Repeat every N frequency units, e.g. 2 = every 2 weeks")
    days_of_week: list[str] | None = Field(None, description="For weekly: ['mon','wed','fri']")
    end_date: datetime | None = Field(None, description="Recurrence ends on/after this date")


class TrainingInstructor(BaseModel):
    """Nested view of instructor_id/instructor_name/instructor_bio/instructor_role.
    Accepted on create/update as an alternative to the flat fields (flattened
    into them); always returned on GET, populated from those same columns."""
    id: UUID | None = None
    name: str | None = None
    bio: str | None = None
    role: str | None = None


class TrainingFaq(BaseModel):
    question: str
    answer: str


class TrainingCheckInRequest(BaseModel):
    participant_email: str = Field(..., description="Enrolled participant's email")
    pass_code: str = Field(..., description="Pass code shown/scanned at the training (matches Training.pass_code)")


# --- Per-enrolment QR check-in (admin/scanner-driven; mirrors the Event registration QR system) ---


class TrainingValidateQrRequest(BaseModel):
    qr_code: str = Field(..., description="QR code scanned from the enrolment")


class TrainingValidateQrResponse(BaseModel):
    valid: bool
    enrolment_id: UUID | None = None
    participant_name: str | None = None
    participant_email: str | None = None
    training_id: UUID | None = None
    training_title: str | None = None
    status: str | None = None
    message: str


class TrainingEnrolCheckInRequest(BaseModel):
    enrolment_id: UUID | None = Field(None, description="Enrolment ID to check in")
    qr_code: str | None = Field(None, description="QR code scanned from the enrolment — alternative to enrolment_id")


class TrainingEnrolCheckInResponse(BaseModel):
    message: str
    enrolment_id: UUID
    participant_name: str | None = None
    participant_email: str | None = None
    status: str
    checked_in_at: str | None = None


class TrainingEnrolUncheckInRequest(BaseModel):
    enrolment_id: UUID | None = Field(None, description="Enrolment ID to undo check-in for")
    qr_code: str | None = Field(None, description="QR code of the enrolment — alternative to enrolment_id")


class TrainingEnrolUncheckInResponse(BaseModel):
    message: str
    enrolment_id: UUID
    participant_name: str | None = None
    participant_email: str | None = None
    status: str
    restored_to: str


class TrainingCheckInPreviewItem(BaseModel):
    enrolment_id: UUID
    participant_name: str | None = None
    participant_email: str | None = None
    status: str
    qr_code: str | None = None
    checked_in_at: str | None = None
    checked_out_at: str | None = None
    can_check_in: bool
    eligibility_reason: str


class TrainingBatchCheckInItem(BaseModel):
    enrolment_id: UUID | None = None
    qr_code: str | None = None


class TrainingBatchCheckInRequest(BaseModel):
    participants: list[TrainingBatchCheckInItem] = Field(..., min_length=1)


class TrainingBatchCheckInResultItem(BaseModel):
    enrolment_id: UUID | str
    participant_name: str | None = None
    participant_email: str | None = None
    status: str
    checked_in_at: str | None = None
    message: str


class TrainingBatchCheckInResponse(BaseModel):
    total: int
    succeeded: int
    failed: int
    results: list[TrainingBatchCheckInResultItem]


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
    instructor_name: str | None = Field(None, description="Instructor display name")
    instructor_bio: str | None = Field(None, description="Instructor biography")
    instructor: TrainingInstructor | None = Field(
        None,
        description="Alternative to the flat instructor_id/instructor_name/instructor_bio fields — "
        "flattened into them. The flat fields win if both are sent.",
    )
    instructor_photo: str | None = Field(None, description="Instructor photo URL")
    instructor_credentials: str | None = Field(None, description="Instructor credentials/qualifications")
    requirements: str | None = None
    prerequisites: list[str] | None = Field(None, description="Course-level prerequisites, e.g. ['Basic Excel']")
    learning_objectives: list[str] | None = Field(None, description="What participants will learn")
    target_audience: str | None = Field(None, description="Who this training is for")
    level: str | None = Field(None, description="beginner|intermediate|advanced|all_levels")
    language: str | None = Field("English", description="Course language")
    subtitle: str | None = Field(None, description="Short tagline, separate from description")
    primary_image: str | None = None
    gallery_images: list | None = None
    promotional_video: str | None = None
    documents: list | None = None
    notes_documents: list[TrainingNoteDocument] | None = Field(
        None, description="Instructor-uploaded notes files, e.g. [{title: 'Week 1 Handout', url: 'https://...'}]"
    )
    delivery_mode: str | None = Field("self_paced", description="self_paced|instructor_led|blended")
    course_type: str | None = Field(None, description="one_day|workshop|virtual|certification")
    duration: str | None = Field(None, description="Duration e.g. 1 day, half_day, custom, 2 weeks")
    start_date: datetime | None = None
    end_date: datetime | None = None
    start_time: str | None = Field(None, description="Daily start time, e.g. 09:00")
    end_time: str | None = Field(None, description="Daily end time, e.g. 17:00")
    venue: str | None = Field(None, description="In-person venue name")
    address: str | None = Field(None, description="Venue address")
    meeting_link: str | None = Field(None, description="Online meeting URL")
    meeting_provider: str | None = Field(None, description="zoom|google_meet|teams|other")
    access_information: str | None = Field(None, description="Login/access details for joining (separate from delivery_instructions)")
    delivery_instructions: str | None = Field(None, description="Instructions for joining/attending")
    offline_access_enabled: bool = Field(False, description="Allow enrolled learners to download lessons for offline viewing")
    recurring: TrainingRecurrence | None = Field(None, description="Recurrence rule, e.g. {frequency: weekly, interval: 1, days_of_week: ['mon','wed']}")
    schedule_exceptions: list[str] | None = Field(None, description="ISO dates skipped from the recurrence")
    instructor_notes: str | None = Field(None, description="Instructor-facing notes, separate from participant-facing instructor_bio")
    session_mode: str | None = Field(None, description="e.g. live|recorded|hybrid")
    check_in: bool = Field(False, description="Enable pass_code/qr_payload self-check-in for this training (server-generated on create)")
    release_rule: dict | None = Field(None, description="Course-wide content release policy: {mode: 'date'|'enrolment_day'|'immediate', date, days}")
    scheduled_publication: datetime | None = Field(None, description="Future timestamp to auto-publish this training")
    randomise: bool = Field(False, description="Randomise assessment question order")
    is_mandatory: bool = Field(False, description="Whole-course mandatory flag (compliance tracking)")
    faqs: list[TrainingFaq] | None = Field(None, description="Frequently asked questions")
    badges: list | None = Field(None, description="Completion badges, e.g. [{name, icon_url}]")
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
    form_configuration_version_id: UUID | None = Field(
        None,
        description="Published Training form configuration version. If omitted, server resolves active form for tenant.",
    )
    custom_values: list | dict | None = Field(
        None,
        description="Custom field values as Record<key,value> or [{field_id,value}]. Core fields still use TrainingCreate scalars.",
    )

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
            "instructor_id": self.instructor_id or (self.instructor.id if self.instructor else None),
            "instructor_name": self.instructor_name or (self.instructor.name if self.instructor else None),
            "instructor_bio": self.instructor_bio or (self.instructor.bio if self.instructor else None),
            "instructor_role": self.instructor.role if self.instructor else None,
            "instructor_photo": self.instructor_photo,
            "instructor_credentials": self.instructor_credentials,
            "requirements": self.requirements,
            "prerequisites": self.prerequisites or [],
            "learning_objectives": self.learning_objectives or [],
            "target_audience": self.target_audience,
            "level": self.level,
            "language": self.language,
            "subtitle": self.subtitle,
            "primary_image": self.primary_image,
            "gallery_images": self.gallery_images or [],
            "promotional_video": self.promotional_video,
            "documents": self.documents or [],
            "notes_documents": [n.model_dump() for n in self.notes_documents] if self.notes_documents else [],
            "delivery_mode": self.delivery_mode,
            "course_type": self.course_type,
            "duration": self.duration,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "venue": self.venue,
            "address": self.address,
            "meeting_link": self.meeting_link,
            "meeting_provider": self.meeting_provider,
            "access_information": self.access_information,
            "delivery_instructions": self.delivery_instructions,
            "offline_access_enabled": self.offline_access_enabled,
            "recurring": self.recurring.model_dump() if self.recurring else None,
            "schedule_exceptions": self.schedule_exceptions or [],
            "instructor_notes": self.instructor_notes,
            "session_mode": self.session_mode,
            "check_in": self.check_in,
            "release_rule": self.release_rule,
            "scheduled_publication": self.scheduled_publication,
            "randomise": self.randomise,
            "is_mandatory": self.is_mandatory,
            "faqs": [f.model_dump() for f in self.faqs] if self.faqs else [],
            "badges": self.badges or [],
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
    instructor_name: str | None = None
    instructor_bio: str | None = None
    instructor: TrainingInstructor | None = None
    instructor_photo: str | None = None
    instructor_credentials: str | None = None
    requirements: str | None = None
    prerequisites: list[str] | None = None
    learning_objectives: list | None = None
    target_audience: str | None = None
    level: str | None = None
    language: str | None = None
    subtitle: str | None = None
    primary_image: str | None = None
    gallery_images: list | None = None
    promotional_video: str | None = None
    documents: list | None = None
    notes_documents: list[TrainingNoteDocument] | None = None
    delivery_mode: str | None = None
    course_type: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    start_time: str | None = None
    end_time: str | None = None
    venue: str | None = None
    address: str | None = None
    meeting_link: str | None = None
    meeting_provider: str | None = None
    access_information: str | None = None
    delivery_instructions: str | None = None
    offline_access_enabled: bool | None = None
    recurring: TrainingRecurrence | None = None
    schedule_exceptions: list[str] | None = None
    instructor_notes: str | None = None
    session_mode: str | None = None
    check_in: bool | None = None
    release_rule: dict | None = None
    scheduled_publication: datetime | None = None
    randomise: bool | None = None
    is_mandatory: bool | None = None
    faqs: list[TrainingFaq] | None = None
    badges: list | None = None
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
    form_configuration_version_id: UUID | None = None
    custom_values: list | dict | None = None

    def to_model_data(self) -> dict:
        data = self.model_dump(exclude_unset=True)
        data.pop("custom_values", None)
        data.pop("form_configuration_version_id", None)
        instructor = data.pop("instructor", None)
        if instructor:
            if data.get("instructor_id") is None and instructor.get("id") is not None:
                data["instructor_id"] = instructor["id"]
            if data.get("instructor_name") is None and instructor.get("name") is not None:
                data["instructor_name"] = instructor["name"]
            if data.get("instructor_bio") is None and instructor.get("bio") is not None:
                data["instructor_bio"] = instructor["bio"]
            if instructor.get("role") is not None:
                data["instructor_role"] = instructor["role"]
        return data


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
    instructor_name: str | None = None
    instructor_bio: str | None = None
    instructor_photo: str | None = None
    instructor_credentials: str | None = None
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
    assignments: list | None = None
    requires_approval: bool | None = None
    access_duration_days: str | None = None
    promo_price: str | None = None
    coupon_code: str | None = None
    requirements: str | None = None
    prerequisites: list | None = None
    learning_objectives: list | None = None
    target_audience: str | None = None
    level: str | None = None
    language: str | None = None
    subtitle: str | None = None
    primary_image: str | None = None
    gallery_images: list | None = None
    promotional_video: str | None = None
    documents: list | None = None
    notes_documents: list | None = None
    duration: str | None = None
    time_zone: str | None = None
    enrolment_start: datetime | None = None
    enrolment_end: datetime | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    start_time: str | None = None
    end_time: str | None = None
    venue: str | None = None
    address: str | None = None
    meeting_link: str | None = None
    meeting_provider: str | None = None
    access_information: str | None = None
    delivery_instructions: str | None = None
    offline_access_enabled: bool | None = None
    recurring: dict | None = None
    schedule_exceptions: list | None = None
    instructor_notes: str | None = None
    session_mode: str | None = None
    check_in: bool | None = None
    pass_code: str | None = None
    qr_payload: str | None = None
    release_rule: dict | None = None
    scheduled_publication: datetime | None = None
    randomise: bool | None = None
    is_mandatory: bool | None = None
    faqs: list | None = None
    badges: list | None = None
    notes_pdf_url: str | None = None
    instructor: TrainingInstructor | None = None
    last_admin_notes: str | None = None
    custom_values: list | None = None
    form_configuration_id: UUID | None = None
    form_configuration_version_id: UUID | None = None


class TrainingListItemResponse(TrainingResponse):
    average_rating: float = Field(0, description="Mean of all review ratings, rounded to 2 decimals.")
    reviews_count: int = Field(0, description="Total number of reviews.")


class TrainingDetailResponse(TrainingResponse):
    enterprise_name: str | None = None
    enrolled_count: int = Field(0, description="Active enrolments (enrolled/active/completed/approved).")
    available_slots: int | None = Field(
        None, description="capacity minus enrolled_count; null when capacity is not set/numeric."
    )
    average_rating: float = Field(0, description="Mean of all review ratings, rounded to 2 decimals.")
    reviews_count: int = Field(0, description="Total number of reviews.")
    waitlist_count: int = Field(0, description="Participants currently on the waitlist.")
    reviews: list[dict] = Field(default_factory=list, description="Most recent reviews (up to 50), newest first.")
    model_config = ConfigDict(from_attributes=True)


class TrainingPaginatedResponse(PaginatedResponse[TrainingListItemResponse]):
    pass


class TrainingAdminActionRequest(BaseModel):
    reason: str = Field(..., min_length=1, description="Admin reason/message for reject or request-changes")


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
    is_downloadable: bool | None = Field(False, description="Allow offline download/caching of this lesson's content_url")
    meeting_link: str | None = Field(None, description="Live-session join link for this lesson (video call URL)")
    file_size: str | None = Field(None, description="Content file size, e.g. '24 MB' — informational, client-supplied")
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
    started_at: str | None = Field(None, description="ISO timestamp when assessment was started — required when time_limit_minutes is set")


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


# ---- Reviews ----

class TrainingReviewCreate(BaseModel):
    rating: int = Field(..., ge=1, le=5, description="Rating from 1 to 5")
    comment: str | None = None
    participant_email: str = Field(..., description="Participant email — must be enrolled to review")


class TrainingReviewResponse(BaseModel):
    id: str
    training_id: str
    rating: int
    comment: str | None = None
    participant_email: str
    verified: bool = True
    created_at: str


class TrainingReviewListResponse(BaseModel):
    reviews: list[TrainingReviewResponse] = Field(default_factory=list)
    average_rating: float = 0
    count: int = 0


# ---- Wishlist ----

class TrainingWishlistItemResponse(BaseModel):
    id: str
    training_id: str
    title: str | None = None
    primary_image: str | None = None
    price: str | None = None
    currency: str | None = None
    average_rating: float = 0
    reviews_count: int = 0
    added_at: str
