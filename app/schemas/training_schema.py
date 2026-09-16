from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.schemas.common_schema import PaginatedResponse


TrainingStatus = str  # draft|published|unpublished|archived|cancelled


class TrainingNoteDocument(BaseModel):
    """An instructor-uploaded notes file — the file itself is uploaded to
    storage by the client; this just records where it lives."""
    model_config = ConfigDict(extra="allow")
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
    sections: list[dict] | None = Field(None, description="Curriculum sections with items or lessons, inline assessments and assignments")
    assessments: list[dict] | None = None
    assignments: list[dict] | None = None
    notes_pdf_url: str | None = None
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
    difficulty_level: str | None = Field(None, description="Contract alias for level — maps onto the level column on create/update")
    language: str | None = Field("English", description="Course language")
    subtitle: str | None = Field(None, description="Short tagline, separate from description")
    primary_image: str | None = None
    gallery_images: list | None = None
    promotional_video: str | None = None
    documents: list | None = None
    notes_documents: list[TrainingNoteDocument] | None = Field(
        None, description="Instructor-uploaded notes files, e.g. [{title: 'Week 1 Handout', url: 'https://...'}]"
    )
    delivery_mode: Literal["online", "physical", "hybrid", "self_paced"] | None = Field("self_paced", description="online|physical|hybrid|self_paced — online requires meeting_link; physical/hybrid require venue; hybrid requires both")
    course_type: str | None = Field(None, description="one_day|workshop|virtual|certification")
    duration: str | None = Field(None, description="Duration e.g. 1 day, half_day, custom, 2 weeks")
    duration_hours: str | None = Field(None, description="Numeric duration in hours, e.g. '20'")
    start_date: datetime | None = None
    end_date: datetime | None = None
    start_time: str | None = Field(None, description="Daily start time, e.g. 09:00")
    end_time: str | None = Field(None, description="Daily end time, e.g. 17:00")
    venue: str | None = Field(None, description="In-person venue name — required when delivery_mode is physical or hybrid")
    address: str | None = Field(None, description="Venue address")
    meeting_link: str | None = Field(None, description="Online meeting URL — required when delivery_mode is online or hybrid")
    meeting_provider: str | None = Field(None, description="zoom|google_meet|teams|other")
    meeting_id: str | None = Field(None, description="Meeting ID, for platforms that separate it from the join link")
    meeting_passcode: str | None = Field(None, description="Meeting passcode, for platforms that separate it from the join link")
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
            "sections": self.sections,
            "assessments": self.assessments,
            "assignments": self.assignments,
            "notes_pdf_url": self.notes_pdf_url,
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
            "level": self.level or self.difficulty_level,
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
            "duration_hours": self.duration_hours,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "venue": self.venue,
            "address": self.address,
            "meeting_link": self.meeting_link,
            "meeting_provider": self.meeting_provider,
            "meeting_id": self.meeting_id,
            "meeting_passcode": self.meeting_passcode,
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
    sections: list[dict] | None = None
    assessments: list[dict] | None = None
    assignments: list[dict] | None = None
    notes_pdf_url: str | None = None
    duration: str | None = None
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
    difficulty_level: str | None = None
    language: str | None = None
    subtitle: str | None = None
    primary_image: str | None = None
    gallery_images: list | None = None
    promotional_video: str | None = None
    documents: list | None = None
    notes_documents: list[TrainingNoteDocument] | None = None
    delivery_mode: Literal["online", "physical", "hybrid", "self_paced"] | None = None
    course_type: str | None = None
    duration_hours: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    start_time: str | None = None
    end_time: str | None = None
    venue: str | None = None
    address: str | None = None
    meeting_link: str | None = None
    meeting_provider: str | None = None
    meeting_id: str | None = None
    meeting_passcode: str | None = None
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
        difficulty_level = data.pop("difficulty_level", None)
        if difficulty_level is not None and data.get("level") is None:
            data["level"] = difficulty_level
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
    duration_hours: str | None = None
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
    meeting_platform: str | None = None  # alias of meeting_provider (contract naming)
    meeting_id: str | None = None
    meeting_passcode: str | None = None
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
    qr_image_base64: str | None = Field(None, description="PNG render of qr_payload as a data: URI — present when qr_payload is set")
    access_type: str | None = Field(None, description="Derived from delivery_mode: online|venue|both|on_demand")
    difficulty_level: str | None = None  # alias of level (contract naming)
    promotional_video_url: str | None = None  # alias of promotional_video (contract naming)
    notes: list | None = None  # alias of notes_documents (contract naming)
    access_expiry_days: str | None = None  # alias of access_duration_days (contract naming)
    current_participants: int | None = None  # alias of enrolled_count (contract naming) — Detail only, requires a query
    moderation_status: str | None = None
    rejection_reason: str | None = None
    published_at: datetime | None = None
    approved_at: datetime | None = None
    archived_at: datetime | None = None
    suspended_at: datetime | None = None
    cancelled_at: datetime | None = None
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
    schedule: str | dict | None = Field(None, description="ISO timestamp or schedule/agenda object")


class TopicCreate(BaseModel):
    model_config = ConfigDict(extra="allow")
    title: str = Field(..., description="Topic title")
    content_url: str | None = Field(None, description="Topic content URL (e.g. PDF, page)")
    videos: list[str] | None = Field(None, description="Topic media video URLs, e.g. uploaded via /trainings/upload")
    documents: list[dict] | None = Field(None, description="Topic attachments: [{url, name, visibility, downloadable}]")
    notes: list[str] | None = Field(None, description="Topic notes URLs (e.g. generated notes PDFs / uploads)")
    duration: str | int | None = Field(None, description="Display duration for the topic")


class LessonCreate(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str = Field("text", description="text|video|audio|webpage|pdf|live|presentation|worksheet|document|venue|exam")
    title: str
    content_url: str | None = None
    topics: list[TopicCreate] | None = Field(None, description="Topics within lesson: [{title, content_url, videos, documents, notes}]")
    duration: int | str | None = Field(None, description="Minutes (int) for content lessons, or a display string (e.g. 'Tue 7:00–7:40 AM') for live/venue lessons")
    is_preview: bool | None = Field(False, description="Preview allowed without enrolment")
    is_draft: bool | None = Field(False, description="Draft mode — hidden until published")
    is_mandatory: bool | None = Field(False, description="Mandatory lesson")
    is_downloadable: bool | None = Field(False, description="Allow offline download/caching of this lesson's content_url")
    meeting_link: str | None = Field(None, description="Live-session join link for this lesson (video call URL)")
    join_meta: str | None = Field(None, description="Join instructions for a live lesson, e.g. 'Opens 10 min before · muted on join'")
    thumbnail_url: str | None = Field(None, description="Thumbnail image URL shown on the lesson card")
    venue: str | None = Field(None, description="In-person venue name for this specific lesson (e.g. 'Restwell Studio · Room B')")
    address: str | None = Field(None, description="Address for this lesson's venue")
    pass_code: str | None = Field(None, description="Check-in pass code for this lesson's venue")
    check_in_window: str | None = Field(None, description="Display string for the check-in window, e.g. 'Opens 8:40 AM · closes 9:20 AM'")
    assessment_id: str | None = Field(None, description="Linked assessment id, for type='exam' lessons — matched against Training.assessments[].id")
    assignment_id: str | None = Field(None, description="Linked assignment id, for type='assignment' lessons — matched against Training.assignments[].id")
    file_size: str | None = Field(None, description="Content file size, e.g. '24 MB' — informational, client-supplied")
    completion_rule: str | None = Field(None, description="Completion rule, e.g. mandatory")
    prerequisites: list | None = Field(None, description="Lesson IDs that must be completed first — sequential learning")
    release_rule: dict | None = Field(None, description="Release: {mode: 'date'|'enrolment_day'|'previous_lesson', date: '2026-01-01', days: 2, lesson_id: '...'}")
    instructor_id: UUID | None = Field(None, description="Lesson instructor allocation")
    videos: list[str] | None = Field(None, description="Lesson media video URLs, e.g. uploaded via /trainings/upload")
    documents: list[dict] | None = Field(None, description="Lesson attachments: [{url, name, visibility, downloadable}]")
    notes: list[str] | None = Field(None, description="Lesson notes URLs (e.g. generated notes PDFs / uploads)")


class AssessmentQuestionCreate(BaseModel):
    model_config = ConfigDict(extra="allow")
    question_text: str = Field(..., description="The question text")
    question_type: str = Field("mcq", description="mcq|multiple_select|true_false|short_answer|essay")
    options: list[str | dict] | None = Field(None, description="Choice strings or {id, label} objects")
    correct_answer: str | None = Field(None, description="Correct answer (for mcq/true_false) or comma-separated for multiple_select")
    points: int = Field(1, description="Points for correct answer")
    explanation: str | None = Field(None, description="Answer explanation")
    reusable: bool | None = Field(False, description="Store in question bank for reuse")


class AssessmentCreate(BaseModel):
    model_config = ConfigDict(extra="allow")
    title: str = Field(..., description="Assessment title")
    type: str = Field("quiz", description="quiz|exam|test|survey")
    instructions: str | None = Field(None, description="Instructions shown before starting the assessment")
    level: str | None = Field(None, description="pre_course|module|final|feedback — where the assessment sits in the course")
    module_id: str | None = Field(None, description="Section/module id this assessment targets (for level='module')")
    lesson_id: str | None = Field(None, description="Lesson id this assessment is attached to")
    time_limit_minutes: int | None = Field(None, description="Time limit — enforced on submit")
    pass_percent: int | None = Field(None, description="Pass threshold as a percentage of total points")
    passing_score: int | None = Field(None, description="Alternative absolute pass score")
    attempts_allowed: int | None = Field(None, description="Number of attempts permitted")
    publish_at: datetime | None = Field(None, description="Scheduled result publication timestamp")
    publication: str | None = Field(None, description="immediate|scheduled — result release mode")
    randomise: bool | None = Field(None, description="Randomise question order")
    is_published: bool | None = Field(True, description="Whether learners can see/attempt the assessment")
    questions: list[AssessmentQuestionCreate] | None = Field(None, description="Inline questions")


class AssessmentSubmitCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "started_at": "2026-09-16T09:00:00Z",
                "answers": [
                    {"question_id": "q1", "answer": "Mars"},
                    {"question_id": "q2", "answer": "2,3"},
                    {"question_id": "q3", "answer": "True"},
                    {"question_id": "q4", "answer": "Paris"},
                    {"question_id": "q5", "answer": "Water evaporates, forms clouds, then falls as rain."},
                ],
            }
        }
    )
    answers: list[dict] = Field(
        ...,
        description=(
            "One entry per answered question. Every entry has exactly two keys, "
            "regardless of question_type: `question_id` (the question's `id` from "
            "GET .../assessments/{aid}) and `answer` (always a plain string). "
            "Payload shape per question_type:\n"
            "- **mcq (Radio)**: `answer` = the exact option text, e.g. `\"Mars\"`. Case-insensitive match.\n"
            "- **multiple_select (Checkbox)**: `answer` = comma-separated option texts, e.g. `\"2,3\"` "
            "(order doesn't matter — compared as a set).\n"
            "- **true_false (Boolean)**: `answer` = `\"True\"` or `\"False\"` (case-insensitive).\n"
            "- **short_answer (Blank Text)**: `answer` = free text. Not auto-scored — held for manual grading.\n"
            "- **essay (Essay)**: `answer` = free text. Not auto-scored — held for manual grading.\n\n"
            "Send one array covering all questions in the assessment in a single request."
        ),
        examples=[
            [
                {"question_id": "q1", "answer": "Mars"},
                {"question_id": "q2", "answer": "2,3"},
                {"question_id": "q3", "answer": "True"},
                {"question_id": "q4", "answer": "Paris"},
                {"question_id": "q5", "answer": "Water evaporates, forms clouds, then falls as rain."},
            ]
        ],
    )
    started_at: str | None = Field(None, description="ISO timestamp when assessment was started — required when time_limit_minutes is set")


class AssessmentSubmitResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "score": 4,
                "passed": False,
                "total_points": 10,
                "feedback": "Pending manual evaluation",
                "assessment_id": "8f14e45f-ceea-4c19-b0a9-3fb6dbe1e6a1",
                "submission_id": "3c7c3e2a-9b1a-4c2e-8e2a-1a2b3c4d5e6f",
                "publication": "immediate",
                "needs_manual": True,
            }
        }
    )
    score: int
    passed: bool
    total_points: int
    feedback: str | None = None
    assessment_id: str = Field(..., description="The assessment's id, echoed back")
    submission_id: str = Field(..., description="Id of the stored submission — pass to the manual-grade/review endpoints")
    publication: str = Field(..., description="When results become visible: immediate|manual|scheduled")
    needs_manual: bool = Field(..., description="True if any short_answer/essay questions were answered and still need manual grading")


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


class AssignmentSubmitFile(BaseModel):
    url: str = Field(..., description="URL to the submitted file/asset")
    name: str = Field("", description="Display name of the submitted file")
    type: str = Field("document", description="image|video|document|link")


class AssignmentSubmitCreate(BaseModel):
    file_url: str | None = Field(None, description="URL to uploaded file (single-file shorthand)")
    files: list[AssignmentSubmitFile] | None = Field(None, description="Multi-media submission — images/videos/documents/links")
    submission_text: str | None = Field(None, description="Text submission content")


class AssignmentSubmitResponse(BaseModel):
    id: str
    submitted_at: str
    grade: int | None = None
    feedback: str | None = None
    files: list[dict] = Field(default_factory=list)


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


class TrainingCompleteLessonRequest(BaseModel):
    lesson_id: str = Field(
        ...,
        validation_alias=AliasChoices("lesson_id", "id"),
        description="ID of the lesson to mark complete. section_id is not required — the backend "
        "locates the lesson by searching every section. 'id' is accepted as a legacy alias for this same field.",
    )
    participant_email: str | None = Field(
        None,
        description="Defaults to the authenticated caller's email if omitted.",
    )

    model_config = ConfigDict(
        json_schema_extra={"example": {"lesson_id": "c4af5aaa-bf67-425e-8393-fb0c3e5bf547"}}
    )


class TrainingCompleteLessonResponse(BaseModel):
    lesson_id: str
    overall_percent: float = Field(..., description="Overall training completion percent, 0-100")
    lessons_done: int
    total_lessons: int
    mandatory_done: int
    mandatory_total: int
    completed_at: str | None = Field(None, description="ISO timestamp — set once the training is fully/mandatorily complete")
    certificate_url: str | None = Field(None, description="Set once completed_at is set")
    resume_lesson: str = Field(..., description="Echoes lesson_id — the lesson to resume from")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "lesson_id": "c4af5aaa-bf67-425e-8393-fb0c3e5bf547",
                "overall_percent": 65.0,
                "lessons_done": 13,
                "total_lessons": 20,
                "mandatory_done": 10,
                "mandatory_total": 12,
                "completed_at": None,
                "certificate_url": None,
                "resume_lesson": "c4af5aaa-bf67-425e-8393-fb0c3e5bf547",
            }
        }
    )


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
    participant_email: str | None = Field(None, description="Deprecated: identity comes from the authenticated user")


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


# ---- Training media upload ----

class TrainingUploadPurpose(str, Enum):
    lesson_video = "lesson_video"
    lesson_pdf = "lesson_pdf"
    lesson_document = "lesson_document"
    audio = "audio"
    image = "image"


class TrainingUploadResponse(BaseModel):
    url: str
    name: str
    size: int
    type: str | None = None
    purpose: str | None = None
