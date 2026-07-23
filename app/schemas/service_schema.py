from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common_schema import (
    AvailabilityScheduleEntry,
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
    created_at: datetime | None = None


class ServiceListItemResponse(ServiceResponse):
    trainer_name: str | None = Field(
        None,
        description="Instructor name (alias of instructor_name).",
    )


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


class ServicePaginatedResponse(PaginatedResponse[ServiceListItemResponse]):
    pass
