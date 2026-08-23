from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common_schema import EventStatus, PaginatedResponse


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
    enterprise_id: UUID = Field(..., description="Enterprise ID")
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
    time_zone: str | None = Field("Asia/Kolkata", description="Time zone")
    registration_cutoff: datetime | None = Field(None, description="Registration cutoff")
    primary_image: str | None = Field(None, description="Primary image URL")
    gallery_images: list | None = Field(None, description="Gallery images")
    videos: list | None = Field(None, description="Videos")
    documents: list | None = Field(None, description="Documents")
    delivery_mode: str | None = Field("in_person", description="In person, Online or Hybrid")
    venue: dict | None = Field(None, description="Venue details")
    meeting_link: str | None = Field(None, description="Meeting link")
    meeting_provider: str | None = Field(None, description="Meeting provider")
    price: str | None = Field(None, description="Price")
    currency: str | None = Field("INR", description="Currency")
    ticket_types: list | None = Field(None, description="Ticket types")
    capacity: str | None = Field(None, description="Capacity")
    min_participants: str | None = None
    max_participants: str | None = None
    registration_open_at: datetime | None = None
    registration_close_at: datetime | None = None
    custom_fields: list | None = None
    sessions: list | None = Field(None, description="Agenda sessions")
    status: EventStatus = Field("draft", description="Event status.")

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
            "time_zone": self.time_zone,
            "registration_cutoff": self.registration_cutoff,
            "primary_image": self.primary_image,
            "gallery_images": self.gallery_images or [],
            "videos": self.videos or [],
            "documents": self.documents or [],
            "delivery_mode": self.delivery_mode,
            "venue": self.venue,
            "meeting_link": self.meeting_link,
            "meeting_provider": self.meeting_provider,
            "price": self.price,
            "currency": self.currency,
            "ticket_types": self.ticket_types or [],
            "capacity": self.capacity,
            "min_participants": self.min_participants,
            "max_participants": self.max_participants,
            "registration_open_at": self.registration_open_at,
            "registration_close_at": self.registration_close_at,
            "custom_fields": self.custom_fields or [],
            "sessions": self.sessions or [],
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
    time_zone: str | None = None
    registration_cutoff: datetime | None = None
    primary_image: str | None = None
    gallery_images: list | None = None
    videos: list | None = None
    documents: list | None = None
    delivery_mode: str | None = None
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
    status: EventStatus | None = None

    def to_model_data(self) -> dict:
        return self.model_dump(exclude_unset=True)


class EventResponse(BaseModel):
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
    organiser_name: str | None = None
    organiser_contact: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    time_zone: str | None = None
    registration_cutoff: datetime | None = None
    primary_image: str | None = None
    gallery_images: list | None = None
    videos: list | None = None
    documents: list | None = None
    delivery_mode: str | None = None
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
    custom_fields: dict | None = None
    ticket_type_id: str | None = None


class EventSessionCreate(BaseModel):
    title: str
    speaker: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    location: str | None = None
    meeting_link: str | None = None


class EventSessionUpdate(BaseModel):
    title: str | None = None
    speaker: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    location: str | None = None
    meeting_link: str | None = None
