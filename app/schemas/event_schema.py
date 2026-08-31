import enum
import uuid
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common_schema import EventStatus, PaginatedResponse

DeliveryMode = str  # in_person|online|hybrid
MeetingProvider = str  # zoom|google_meet|teams|other


class EventTicketType(BaseModel):
    id: str | None = Field(None, description="Ticket type ID (auto-generated if missing)")
    name: str = Field(..., description="Ticket name e.g. Early Bird, Standard")
    price: str = Field(..., description="Standard price")
    currency: str | None = Field("INR", description="Currency")
    capacity: int | None = Field(None, description="Capacity for this ticket type")
    early_bird_price: str | None = Field(None, description="Early-bird price")
    early_bird_until: datetime | None = Field(None, description="Early-bird deadline")
    promo_price: str | None = Field(None, description="Promotional price")
    description: str | None = None


class EventCheckoutRequest(BaseModel):
    participant_name: str = Field(..., description="Buyer name")
    participant_email: str = Field(..., description="Buyer email")
    ticket_type_id: str = Field(..., description="Ticket type ID")
    quantity: int = Field(1, ge=1, description="Quantity")
    payment_provider: str | None = Field("marketplace", description="marketplace|merchant")


class EventOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    event_id: UUID
    participant_name: str
    participant_email: str
    ticket_type_id: str | None = None
    quantity: int
    amount: str
    currency: str
    payment_status: str
    status: str
    created_at: datetime | None = None


class EventRefundRequest(BaseModel):
    reason: str | None = Field(None, description="Reason for refund")
    amount: str | None = Field(None, description="Partial amount if partial refund")


class EventVenue(BaseModel):
    address: str | None = Field(None, description="Street address")
    city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None
    latitude: float | None = Field(None, description="Map latitude")
    longitude: float | None = Field(None, description="Map longitude")
    instructions: str | None = Field(None, description="Venue instructions / how to reach")
    map_url: str | None = Field(None, description="Map link")


class EventCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "tenant_id": "550e8400-e29b-41d4-a716-446655440000",
                "enterprise_id": "550e8400-e29b-41d4-a716-446655440001",
                "location_id": "550e8400-e29b-41d4-a716-446655440002",
                "title": "Wellness Summit 2026",
                "description": "Annual wellness event",
                "category": "Fitness & Wellness",
                "status": "draft",
            }
        }
    )

    tenant_id: UUID | None = Field(None, description="Tenant identifier.")
    enterprise_id: UUID | None = Field(None, description="Enterprise ID. If omitted, event is owned by the authenticated tenant.")
    location_id: UUID | None = Field(None, description="Enterprise location ID")
    title: str = Field(..., description="Event title")
    description: str | None = Field(None, description="Event description")
    category: str = Field(..., description="Event category")
    subcategory: str | None = Field(None, description="Event subcategory")
    tags: list[str] | None = Field(None, description="Event tags")
    organiser_name: str | None = Field(None, description="Organiser name")
    organiser_contact: str | None = Field(None, description="Organiser contact")
    start_date: datetime = Field(..., description="Event start date")
    end_date: datetime = Field(..., description="Event end date")
    duration_type: str = Field("custom", description="one_day|half_day|custom")
    time_zone: str | None = Field("Asia/Kolkata", description="Time zone")
    registration_cutoff: datetime | None = Field(None, description="Registration cutoff")
    primary_image: str | None = Field(None, description="Primary image URL")
    gallery_images: list | None = Field(None, description="Gallery images")
    videos: list | None = Field(None, description="Videos")
    documents: list | None = Field(None, description="Documents")
    delivery_mode: DeliveryMode = Field("in_person", description="in_person|online|hybrid — display as In Person/Online/Hybrid")
    venue: EventVenue | dict | None = Field(None, description="Venue: address, city, latitude, longitude, instructions, map_url")
    meeting_link: str | None = Field(None, description="Manual meeting link (auto-generated if delivery_mode online/hybrid and meeting_provider set)")
    meeting_provider: MeetingProvider | None = Field(None, description="zoom|google_meet|teams|other")
    price: str | None = Field(None, description="Price")
    currency: str | None = Field("INR", description="Currency")
    ticket_types: list[EventTicketType] | list | None = Field(None, description="Ticket types with price/capacity/early-bird/promo")
    capacity: str | None = Field(None, description="Capacity")
    min_participants: str | None = None
    max_participants: str | None = None
    registration_open_at: datetime | None = None
    registration_close_at: datetime | None = None
    custom_fields: list | None = None
    sessions: list | None = Field(None, description="Agenda sessions")
    status: EventStatus = Field("draft", description="Event status.")

    def _normalize_ticket_types(self) -> list:
        normalized: list[dict] = []
        for raw in self.ticket_types or []:
            if isinstance(raw, EventTicketType):
                d = raw.model_dump()
            elif isinstance(raw, dict):
                d = dict(raw)
            else:
                continue
            if not d.get("id"):
                d["id"] = str(uuid.uuid4())
            # coerce capacity to int if possible, keep as is
            normalized.append(d)
        return normalized

    def _normalize_sessions(self) -> list:
        # Ensure each embedded session has id and session_date string, sorted by (session_date, start_time)
        normalized: list[dict] = []
        for raw in self.sessions or []:
            s = dict(raw) if isinstance(raw, dict) else {}
            # generate id if missing
            if not s.get("id"):
                s["id"] = str(uuid.uuid4())
            # normalize session_date date -> ISO string
            sd = s.get("session_date")
            if hasattr(sd, "isoformat"):
                s["session_date"] = sd.isoformat()
            elif sd is not None:
                s["session_date"] = str(sd)
            normalized.append(s)
        # sort by (session_date, start_time) for consistency with dedicated add_session_service
        def _key(x):
            return (str(x.get("session_date") or ""), str(x.get("start_time") or ""))
        return sorted(normalized, key=_key)

    def _venue_dict(self):
        if isinstance(self.venue, EventVenue):
            return self.venue.model_dump(exclude_none=True)
        return self.venue

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
            "organiser_name": self.organiser_name,
            "organiser_contact": self.organiser_contact,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "duration_type": self.duration_type,
            "time_zone": self.time_zone,
            "registration_cutoff": self.registration_cutoff,
            "primary_image": self.primary_image,
            "gallery_images": self.gallery_images or [],
            "videos": self.videos or [],
            "documents": self.documents or [],
            "delivery_mode": self.delivery_mode,
            "venue": self._venue_dict(),
            "meeting_link": self.meeting_link,
            "meeting_provider": self.meeting_provider,
            "price": self.price,
            "currency": self.currency,
            "ticket_types": self._normalize_ticket_types(),
            "capacity": self.capacity,
            "min_participants": self.min_participants,
            "max_participants": self.max_participants,
            "registration_open_at": self.registration_open_at,
            "registration_close_at": self.registration_close_at,
            "custom_fields": self.custom_fields or [],
            "sessions": self._normalize_sessions(),
            "status": self.status,
        }


class EventUpdate(BaseModel):
    tenant_id: UUID | None = None
    location_id: UUID | None = None
    title: str | None = None
    description: str | None = None
    category: str | None = None
    subcategory: str | None = None
    tags: list[str] | None = None
    organiser_name: str | None = None
    organiser_contact: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    duration_type: str | None = None
    time_zone: str | None = None
    registration_cutoff: datetime | None = None
    primary_image: str | None = None
    gallery_images: list | None = None
    videos: list | None = None
    documents: list | None = None
    delivery_mode: DeliveryMode | None = None
    venue: EventVenue | dict | None = None
    meeting_link: str | None = None
    meeting_provider: MeetingProvider | None = None
    price: str | None = None
    currency: str | None = None
    ticket_types: list | None = None
    capacity: str | None = None
    min_participants: str | None = None
    max_participants: str | None = None
    registration_open_at: datetime | None = None
    registration_close_at: datetime | None = None
    custom_fields: list | None = None
    sessions: list | None = None
    status: EventStatus | None = None

    def to_model_data(self) -> dict:
        data = self.model_dump(exclude_unset=True)
        if "sessions" in data and data["sessions"] is not None:
            normalized: list[dict] = []
            for raw in data["sessions"] or []:
                s = dict(raw) if isinstance(raw, dict) else {}
                if not s.get("id"):
                    s["id"] = str(uuid.uuid4())
                sd = s.get("session_date")
                if hasattr(sd, "isoformat"):
                    s["session_date"] = sd.isoformat()
                elif sd is not None:
                    s["session_date"] = str(sd)
                normalized.append(s)
            def _key(x):
                return (str(x.get("session_date") or ""), str(x.get("start_time") or ""))
            data["sessions"] = sorted(normalized, key=_key)
        return data


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None = None
    enterprise_id: UUID | None = None
    location_id: UUID | None = None
    title: str
    description: str | None = None
    category: str
    subcategory: str | None = None
    tags: list | None = None
    organiser_name: str | None = None
    organiser_contact: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    duration_type: str | None = None
    time_zone: str | None = None
    registration_cutoff: datetime | None = None
    primary_image: str | None = None
    gallery_images: list | None = None
    videos: list | None = None
    documents: list | None = None
    delivery_mode: str | None = None
    delivery_mode_display: str | None = Field(None, description="In Person|Online|Hybrid")
    venue: dict | None = None
    meeting_link: str | None = None
    meeting_provider: str | None = None
    price: str | None = None
    currency: str | None = None
    ticket_types: list | None = None
    capacity: str | None = None
    min_participants: str | None = None
    max_participants: str | None = None
    registration_open_at: datetime | None = None
    registration_close_at: datetime | None = None
    custom_fields: list | None = None
    sessions: list | None = None
    status: str
    is_deleted: bool | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    available_seats: int | None = Field(None, description="Available seats (capacity - confirmed)")
    is_full: bool | None = Field(None, description="Whether event is at capacity")
    registration_open: bool | None = Field(None, description="Whether registration window is open")


class EventListItemResponse(EventResponse):
    pass


class EventDetailResponse(EventResponse):
    enterprise_name: str | None = Field(None, description="Owning enterprise short name.")

    model_config = ConfigDict(from_attributes=True)


class EventPaginatedResponse(PaginatedResponse[EventListItemResponse]):
    pass


class EventStatusUpdate(BaseModel):
    status: EventStatus = Field(..., description="Target status")
    reason: str | None = Field(None, description="Reason for status change")


class EventRegistrationCreate(BaseModel):
    participant_name: str = Field(..., description="Participant name")
    participant_email: str = Field(..., description="Participant email")
    custom_fields: dict | None = Field(None, description="Custom fields / participant questions")
    ticket_type_id: str | None = None
    group_size: int | None = Field(None, ge=1, description="Group size for group registration (1=individual)")
    group_members: list[dict] | None = Field(None, description="List of {name, email} for group members")


class EventSessionCreate(BaseModel):
    session_date: date = Field(..., description="Session date (YYYY-MM-DD), must be within Event start_date..end_date", examples=["2026-09-02"])
    title: str
    speaker: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    location: str | None = None
    meeting_link: str | None = None


class EventSessionUpdate(BaseModel):
    session_date: date | None = Field(None, description="Session date (YYYY-MM-DD)")
    title: str | None = None
    speaker: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    location: str | None = None
    meeting_link: str | None = None


class EventSessionResponse(BaseModel):
    id: str
    session_date: date | None = None
    title: str
    speaker: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    location: str | None = None
    meeting_link: str | None = None


class EventCheckInRequest(BaseModel):
    registration_id: UUID | None = Field(None, description="Registration ID to check in")
    qr_code: str | None = Field(None, description="QR code scanned from participant badge")
    session_id: str | None = Field(None, description="Optional: specific session ID for per-session attendance")
    method: str | None = Field("manual", description="Check-in method: manual, qr_code, nfc")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "registration_id": "550e8400-e29b-41d4-a716-446655440000",
                "qr_code": None,
                "session_id": "session_123",
                "method": "manual"
            }
        }
    )


class EventCheckInResponse(BaseModel):
    message: str
    registration_id: UUID
    participant_name: str | None = None
    participant_email: str | None = None
    status: str
    checked_in_at: str | None = None
    session_id: str | None = None


class EventAttendanceItem(BaseModel):
    registration_id: UUID
    participant_name: str
    participant_email: str
    status: str
    checked_in_at: str | None = None
    checked_in_by: UUID | None = None
    checked_out_at: str | None = None
    session_id: str | None = None
    ticket_type_id: str | None = None


class EventAttendanceResponse(BaseModel):
    event_id: UUID
    total_registered: int
    total_attended: int
    total_no_show: int
    attendance_by_session: dict[str, dict] | None = None
    participants: list[EventAttendanceItem]


class EventUncheckInRequest(BaseModel):
    registration_id: UUID | None = Field(None, description="Registration ID to uncheck")
    qr_code: str | None = Field(None, description="QR code of registration to uncheck")
    reason: str | None = Field(None, description="Reason for undoing check-in")


class EventUncheckInResponse(BaseModel):
    message: str
    registration_id: UUID
    participant_name: str | None = None
    participant_email: str | None = None
    status: str
    restored_to: str


class EventCheckOutRequest(BaseModel):
    registration_id: UUID | None = Field(None, description="Registration ID to check out")
    qr_code: str | None = Field(None, description="QR code of registration to check out")
    session_id: str | None = Field(None, description="Optional: specific session ID for per-session check-out")


class EventCheckOutResponse(BaseModel):
    message: str
    registration_id: UUID
    participant_name: str | None = None
    participant_email: str | None = None
    status: str
    checked_in_at: str | None = None
    checked_out_at: str | None = None
    session_id: str | None = None


class EventQRValidateResponse(BaseModel):
    valid: bool
    registration_id: UUID | None = None
    participant_name: str | None = None
    participant_email: str | None = None
    status: str | None = None
    event_id: UUID | None = None
    event_title: str | None = None
    ticket_type_id: str | None = None
    message: str


class EventAnnouncementCreate(BaseModel):
    title: str | None = None
    message: str = Field(..., description="Announcement message")
    recipient_type: str = Field("all", description="all|registered|specific")
    channels: list[str] = Field(["in_app"], description="in_app|push|email|sms")
    metadata: dict | None = Field(None, description="Additional metadata")


class EventAnnouncementResponse(BaseModel):
    id: str
    event_id: UUID
    sent_by: str | None = None
    recipient_count: int
    created_at: str
    title: str | None = None
    message: str


class EventReportType(str, enum.Enum):
    registration = "registration"
    attendance = "attendance"
    feedback = "feedback"
    revenue = "revenue"


class EventReportResponse(BaseModel):
    event_id: UUID
    type: EventReportType
    data: dict


class EventSummaryResponse(BaseModel):
    total_events: int
    by_status: dict[str, int]
    by_category: dict[str, int]
    by_delivery_mode: dict[str, int]
    upcoming_events: int
    past_events: int
    total_registrations: int
    total_attended: int
    average_rating: float | None = None


# --- EventCategory CRUD schemas ---

class EventCategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Category name")
    parent_id: UUID | None = Field(None, description="Parent category ID (for subcategories)")
    description: str | None = Field(None, description="Category description")


class EventCategoryUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100, description="Category name")
    description: str | None = Field(None, description="Category description")


class EventCategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    parent_id: UUID | None = None
    description: str | None = None
    created_at: datetime | None = None


# --- Waitlist / Feedback / Announcement standardised schemas ---

class EventWaitlistResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    participant_name: str
    participant_email: str
    created_at: datetime | None = None


class EventFeedbackResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    participant_email: str | None = None
    form_id: str | None = None
    answers: dict | None = None
    rating: str | None = None
    comment: str | None = None
    is_review: bool = False
    moderation_status: str = "pending"
    created_at: datetime | None = None


# --- Batch Check-in ---

class EventBatchCheckInItem(BaseModel):
    registration_id: UUID | None = Field(None, description="Registration ID")
    qr_code: str | None = Field(None, description="QR code")
    session_id: str | None = Field(None, description="Optional session ID")


class EventBatchCheckInRequest(BaseModel):
    participants: list[EventBatchCheckInItem] = Field(..., min_length=1, description="List of participants to check in")


class EventBatchCheckInResultItem(BaseModel):
    registration_id: UUID
    participant_name: str | None = None
    participant_email: str | None = None
    status: str
    checked_in_at: str | None = None
    message: str


class EventBatchCheckInResponse(BaseModel):
    total: int
    succeeded: int
    failed: int
    results: list[EventBatchCheckInResultItem]
