from datetime import date, datetime
from uuid import UUID

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from app.services.review_common import edit_label, is_public, public_name, shown_reason, status_label

DeliveryMode = Literal["in_person", "virtual", "hybrid"]


class CancellationRules(BaseModel):
    """The structured cancellation policy a booking follows (the free-text `cancellation_policy` stays as the wording shown to people)."""

    free_cancellation_hours: int = Field(24, ge=0, le=24 * 90, description="A customer can cancel on their own up to this many hours before the start.")
    refund_percent_before: int = Field(100, ge=0, le=100, description="Share refunded when cancelled inside the free window.")
    refund_percent_after: int = Field(0, ge=0, le=100, description="Share refunded when an admin approves a cancellation requested after the window.")
    allow_request_after_window: bool = Field(True, description="After the window a customer can still ask (an admin decides); false = not allowed.")



class BlockedPeriod(BaseModel):
    """Dates (and optionally a time range on each of them) when the service cannot be booked: a holiday, a closure, a training day."""

    start_date: date
    end_date: date | None = Field(None, description="Last blocked day (inclusive). Default: start_date.")
    start_time: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="HH:MM in the service's time zone. Both times empty = the whole day.")
    end_time: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    reason: str | None = Field(None, max_length=200)

    @model_validator(mode="after")
    def _check(self):
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("send both start_time and end_time, or neither for whole days")
        if self.start_time is not None and self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


def _check_time_zone(value):
    if value is None:
        return value
    from app.utils.event_utils import is_valid_time_zone

    if not is_valid_time_zone(value):
        raise ValueError("time_zone must be an IANA name such as Asia/Kolkata")
    return value.strip()

from app.schemas.common_schema import (
    PaginationMeta,
    AvailabilityScheduleEntry,
    CatalogReviewItem,
    EntityStatus,
    PaginatedResponse,
    ServiceAvailabilityDay,
)


class ServiceCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "tenant_id": "550e8400-e29b-41d4-a716-446655440000",
                "enterprise_id": "550e8400-e29b-41d4-a716-446655440001",
                "location_id": "550e8400-e29b-41d4-a716-446655440002",
                "service_name": "Kids Fitness Program",
                "description": "Weekly fitness classes for children",
                "category": "Fitness",
                "duration_minutes": 60,
                "price": 150.0,
                "currency": "USD",
                "availability": [
                    {
                        "day": "monday",
                        "is_available": True,
                        "start_time": "09:00",
                        "end_time": "17:00",
                        "slot_length": "60",
                    }
                ],
                "status": "draft",
            }
        }
    )

    tenant_id: UUID | None = Field(None, description="Tenant identifier.")
    enterprise_id: UUID = Field(..., description="Enterprise ID")
    location_id: UUID | None = Field(None, description="Enterprise location ID")
    service_name: str = Field(..., description="Service name")
    description: str | None = Field(None, description="Service description")
    category: str = Field(..., description="Service category")
    duration_minutes: int = Field(..., description="Duration in minutes")
    price: float = Field(..., description="Service price")
    currency: str | None = Field("USD", description="ISO currency code")
    availability: list[AvailabilityScheduleEntry] | None = Field(
        default_factory=list,
        description="Weekly availability schedule.",
    )
    status: EntityStatus = Field("draft", description="Service lifecycle status.")
    service_description: str | None = None
    service_category: str | None = None
    service_price: float | None = None
    duration: int | None = None
    service_type: str | None = Field(
        None,
        description="Service type label (e.g. group_class, personal_training).",
    )
    banner_image: str | None = Field(
        None,
        description="URL of the service banner image.",
    )
    availability_status: bool = Field(default=True, description="Availability flag")
    service_status: bool | None = None
    max_participants: int | None = None
    provider_name: str | None = None
    provider_user_id: UUID | None = Field(
        None,
        description="Identifier of the tenant internal user assigned as provider/instructor.",
    )
    instructor_name: str | None = None
    delivery_format: str | None = None
    package_price: float | None = None
    cancellation_policy: str | None = None
    availability_schedule: list[AvailabilityScheduleEntry] | None = None
    # booking settings (docs/service-bookings.md)
    delivery_modes: list[DeliveryMode] | None = Field(None, description="in_person, virtual, hybrid (both venue and meeting). Several can be offered.")
    booking_enabled: bool | None = Field(None, description="Customers can book this service (default true).")
    requires_approval: bool | None = Field(None, description="Retired: accepted and stored for compatibility, but new bookings never wait for approval.")
    time_zone: str | None = Field(None, description="IANA zone the weekly schedule is written in (default Asia/Kolkata).")
    cancellation_rules: CancellationRules | None = None
    venue_instructions: str | None = Field(None, max_length=1000, description="Arrival / check-in instructions for an in-person booking.")
    meeting_url: str | None = Field(None, max_length=1000, description="The meeting link (Zoom, Google Meet, ...) shown to the customer shortly before the start.")
    meeting_provider: str | None = Field(None, max_length=30)
    meeting_passcode: str | None = Field(None, max_length=100)
    meeting_instructions: str | None = Field(None, max_length=1000)
    min_notice_minutes: int | None = Field(None, ge=0, le=60 * 24 * 90, description="Earliest a slot can be booked, in minutes from now. Null = platform default (60).")
    max_advance_days: int | None = Field(None, ge=1, le=730, description="Furthest ahead a slot can be booked, in days. Null = platform default (60).")
    blocked_periods: list[BlockedPeriod] | None = Field(None, max_length=200, description="Holidays and blocked dates / time ranges. Existing bookings in them are kept.")

    _tz = field_validator("time_zone")(_check_time_zone)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_aliases(cls, data):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if normalized.get("service_description") and not normalized.get("description"):
            normalized["description"] = normalized["service_description"]
        if normalized.get("service_category") and not normalized.get("category"):
            normalized["category"] = normalized["service_category"]
        if normalized.get("service_price") is not None and normalized.get("price") is None:
            normalized["price"] = normalized["service_price"]
        if normalized.get("duration") is not None and normalized.get("duration_minutes") is None:
            normalized["duration_minutes"] = normalized["duration"]
        if normalized.get("availability_schedule") and not normalized.get("availability"):
            normalized["availability"] = normalized["availability_schedule"]
        if "service_status" in normalized and "status" not in normalized:
            normalized["status"] = (
                "active" if normalized["service_status"] else "inactive"
            )
        return normalized

    def to_model_data(self) -> dict:
        schedule = self.availability or self.availability_schedule
        schedule_payload = None
        if schedule is not None:
            schedule_payload = [
                entry.model_dump() if hasattr(entry, "model_dump") else entry
                for entry in schedule
            ]
        return {
            "tenant_id": self.tenant_id,
            "enterprise_id": self.enterprise_id,
            "location_id": self.location_id,
            "service_name": self.service_name,
            "service_description": self.description or self.service_description,
            "service_category": self.category or self.service_category,
            "service_price": self.price if self.price is not None else self.service_price,
            "duration": self.duration_minutes if self.duration_minutes is not None else self.duration,
            "currency": self.currency,
            "availability_schedule": schedule_payload,
            "availability_status": self.availability_status,
            "status": self.status,
            "service_status": self.status == "active",
            "service_type": self.service_type,
            "banner_image": self.banner_image,
            "max_participants": self.max_participants,
            "provider_name": self.provider_name,
            "provider_user_id": self.provider_user_id,
            "instructor_name": self.instructor_name,
            "delivery_format": self.delivery_format,
            "package_price": self.package_price,
            "cancellation_policy": self.cancellation_policy,
            "delivery_modes": list(self.delivery_modes) if self.delivery_modes else None,
            "booking_enabled": True if self.booking_enabled is None else self.booking_enabled,
            "requires_approval": bool(self.requires_approval),
            "time_zone": self.time_zone or "Asia/Kolkata",
            "cancellation_rules": self.cancellation_rules.model_dump() if self.cancellation_rules else None,
            "venue_instructions": self.venue_instructions,
            "meeting_url": self.meeting_url,
            "meeting_provider": self.meeting_provider,
            "meeting_passcode": self.meeting_passcode,
            "meeting_instructions": self.meeting_instructions,
            "min_notice_minutes": self.min_notice_minutes,
            "max_advance_days": self.max_advance_days,
            "blocked_periods": [p.model_dump(mode="json") for p in self.blocked_periods] if self.blocked_periods is not None else None,
        }


class ServiceUpdate(BaseModel):
    tenant_id: UUID | None = None
    location_id: UUID | None = None
    service_name: str | None = None
    description: str | None = None
    category: str | None = None
    duration_minutes: int | None = None
    price: float | None = None
    currency: str | None = None
    availability: list[AvailabilityScheduleEntry] | None = None
    status: EntityStatus | None = None
    service_description: str | None = None
    service_category: str | None = None
    service_price: float | None = None
    duration: int | None = None
    service_type: str | None = None
    banner_image: str | None = None
    availability_status: bool | None = None
    service_status: bool | None = None
    max_participants: int | None = None
    provider_name: str | None = None
    provider_user_id: UUID | None = Field(
        None,
        description="Identifier of the tenant internal user assigned as provider/instructor.",
    )
    instructor_name: str | None = None
    delivery_format: str | None = None
    package_price: float | None = None
    cancellation_policy: str | None = None
    availability_schedule: list[AvailabilityScheduleEntry] | None = None
    delivery_modes: list[DeliveryMode] | None = None
    booking_enabled: bool | None = None
    requires_approval: bool | None = None
    time_zone: str | None = None
    cancellation_rules: CancellationRules | None = None
    venue_instructions: str | None = Field(None, max_length=1000)
    meeting_url: str | None = Field(None, max_length=1000)
    meeting_provider: str | None = Field(None, max_length=30)
    meeting_passcode: str | None = Field(None, max_length=100)
    meeting_instructions: str | None = Field(None, max_length=1000)
    min_notice_minutes: int | None = Field(None, ge=0, le=60 * 24 * 90, description="Earliest a slot can be booked, in minutes from now. Null = platform default (60).")
    max_advance_days: int | None = Field(None, ge=1, le=730, description="Furthest ahead a slot can be booked, in days. Null = platform default (60).")
    blocked_periods: list[BlockedPeriod] | None = Field(None, max_length=200, description="Holidays and blocked dates / time ranges. Existing bookings in them are kept.")

    _tz = field_validator("time_zone")(_check_time_zone)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_aliases(cls, data):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if normalized.get("service_description") and not normalized.get("description"):
            normalized["description"] = normalized["service_description"]
        if normalized.get("service_category") and not normalized.get("category"):
            normalized["category"] = normalized["service_category"]
        if normalized.get("service_price") is not None and normalized.get("price") is None:
            normalized["price"] = normalized["service_price"]
        if normalized.get("duration") is not None and normalized.get("duration_minutes") is None:
            normalized["duration_minutes"] = normalized["duration"]
        if normalized.get("availability_schedule") and not normalized.get("availability"):
            normalized["availability"] = normalized["availability_schedule"]
        if "service_status" in normalized and "status" not in normalized:
            normalized["status"] = (
                "active" if normalized["service_status"] else "inactive"
            )
        return normalized

    def to_model_data(self) -> dict:
        data = self.model_dump(exclude_unset=True)
        mapped: dict = {}
        field_map = {
            "description": "service_description",
            "category": "service_category",
            "price": "service_price",
            "duration_minutes": "duration",
            "availability": "availability_schedule",
        }
        for key, value in data.items():
            if key == "availability" or key == "availability_schedule":
                if value is not None:
                    mapped["availability_schedule"] = [
                        entry.model_dump() if hasattr(entry, "model_dump") else entry
                        for entry in value
                    ]
            elif key in field_map:
                mapped[field_map[key]] = value
            elif key not in {
                "service_description",
                "service_category",
                "service_price",
                "duration",
                "availability_schedule",
            }:
                mapped[key] = value
        if mapped.get("blocked_periods") is not None:
            mapped["blocked_periods"] = [p.model_dump(mode="json") for p in self.blocked_periods]
        if "status" in mapped:
            mapped["service_status"] = mapped["status"] == "active"
        return mapped


class ServiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None = None
    enterprise_id: UUID
    location_id: UUID | None = None
    service_name: str
    description: str | None = None
    category: str
    duration_minutes: int
    price: float
    currency: str | None
    availability: list[AvailabilityScheduleEntry] | None = None
    status: EntityStatus
    service_description: str | None = None
    service_category: str | None = None
    service_price: float | None = None
    duration: int | None = None
    service_type: str | None = None
    banner_image: str | None = None
    images: list[str] = Field(default_factory=list, description="The banner as a list of hosted http(s) URLs (empty when there is none). Never base64.")
    min_notice_minutes: int | None = None
    max_advance_days: int | None = None
    blocked_periods: list[BlockedPeriod] | None = None
    availability_status: bool
    service_status: bool | None = None
    max_participants: int | None = None
    provider_name: str | None = None
    provider_user_id: UUID | None = None
    instructor_name: str | None = None
    delivery_format: str | None = None
    package_price: float | None = None
    cancellation_policy: str | None = None
    availability_schedule: list[AvailabilityScheduleEntry] | None = None
    delivery_modes: list[DeliveryMode] | None = None
    booking_enabled: bool | None = None
    requires_approval: bool | None = Field(None, description="Retired: has no effect on new bookings (they are confirmed automatically, or after payment).")
    time_zone: str | None = None
    cancellation_rules: CancellationRules | None = None
    venue_instructions: str | None = None
    # meeting_url / meeting_provider / meeting_passcode / meeting_instructions are deliberately NOT here: this answer is the public catalog.
    # Staff read them with GET /services/{id}/booking-settings; a customer gets them on their own booking, shortly before the start.
    created_at: datetime | None = None


class ServiceListItemResponse(ServiceResponse):
    enterprise_name: str | None = Field(
        None,
        description="Short name of the owning enterprise.",
    )
    trainer_name: str | None = Field(
        None,
        description="Instructor name (alias of instructor_name).",
    )
    rating: float = Field(
        0,
        description="Mean of the approved review ratings, 0 when there are none (check reviews_count).",
    )
    reviews_count: int = Field(0, description="Number of approved reviews.")


class ServiceDetailResponse(ServiceResponse):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440001",
                "enterprise_id": "550e8400-e29b-41d4-a716-446655440000",
                "enterprise_name": "Spin Health",
                "service_name": "Kids Fitness Program",
                "category": "Fitness",
                "type": "group_class",
                "banner_image": "https://cdn.example.com/services/kids-fitness.png",
                "trainer_name": "Coach Alex",
            }
        },
    )

    enterprise_name: str | None = Field(
        None,
        description="Short name of the owning enterprise.",
    )
    type: str | None = Field(
        None,
        description="Service type (from service_type, falling back to service_category).",
    )
    trainer_name: str | None = Field(
        None,
        description="Instructor name (alias of instructor_name).",
    )
    format: str | None = Field(
        None,
        description="Delivery format (alias of delivery_format).",
    )
    availability: list[ServiceAvailabilityDay] = Field(
        default_factory=list,
        description="Weekly availability with generated time slots.",
    )
    reviews: list[CatalogReviewItem] = Field(
        default_factory=list,
        description="Latest approved reviews of this service (up to 20), newest first.",
    )
    rating: float = Field(
        0,
        description="Mean of the approved review ratings, 0 when there are none (check reviews_count).",
    )
    reviews_count: int = Field(0, description="Number of approved reviews of this service.")


class ServicePaginatedResponse(PaginatedResponse[ServiceListItemResponse]):
    pass


class ServiceReviewCreate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"rating": 5, "comment": "Really helpful session."}})
    rating: int = Field(..., ge=1, le=5, description="1-5 stars")
    comment: str | None = Field(None, max_length=2000)


class ServiceReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    service_id: UUID
    user_id: UUID
    reviewer_name: str | None = None
    rating: int
    comment: str | None = None
    is_verified_purchase: bool = Field(..., description="Always false today — no booking/purchase record exists yet to verify a Service reviewer against")
    moderation_status: str = Field(..., description="pending|approved|rejected — only approved reviews are shown in the public list. A new review is approved at once")
    created_at: datetime
    updated_at: datetime

    moderation_reason: str | None = Field(None, exclude=True)

    @computed_field(description="The same as moderation_status: approved | pending | rejected | deleted")
    @property
    def status(self) -> str:
        return self.moderation_status or "pending"

    @computed_field(description="Why a Super Admin rejected, held back (pending) or deleted this review. null otherwise")
    @property
    def reason(self) -> str | None:
        return shown_reason(self.moderation_status, self.moderation_reason)

    @computed_field(description="What the customer sees: Approved | Waiting for Approval | Not Approved")
    @property
    def status_label(self) -> str:
        return status_label(self.moderation_status)

    @computed_field(description="True only for an approved review (it is the only kind in the public list)")
    @property
    def is_public(self) -> bool:
        return is_public(self.moderation_status)

    @computed_field(description="The edit form's button: Resubmit Review for a rejected review, else Save Changes")
    @property
    def edit_label(self) -> str:
        return edit_label(self.moderation_status)

    @field_validator("rating", mode="before")
    @classmethod
    def _rating_to_int(cls, value):
        return int(value)

    @field_validator("reviewer_name", mode="after")
    @classmethod
    def _hide_email_as_name(cls, value):
        return public_name(value)


class ServiceReviewListResponse(BaseModel):
    my_review: ServiceReviewResponse | None = Field(None, description="The signed-in caller's own review in any status (approved | pending | rejected | deleted); null when they have none or are not signed in. Not repeated in `reviews`; still counted in `total` and the average")
    reviews: list[ServiceReviewResponse]
    average_rating: float | None = Field(None, description="Average of approved reviews' ratings; null when there are none")
    total: int = Field(..., description="Count of approved reviews")
    rating_distribution: dict[str, int] = Field(default_factory=dict, description="Approved reviews per star: {\"5\": n, \"4\": n, ...}")
    pagination: PaginationMeta | None = Field(None, description="total = reviews after the filters; page/page_size when paging, else everything on one page")


class ServiceReviewModerateRequest(BaseModel):
    action: str = Field(..., description="approved|rejected|pending")
    reason: str | None = Field(None, description="Required when action is rejected or pending (3 to 500 characters, else 422). Not needed for approved.")
