from datetime import date, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common_schema import AvailabilityScheduleEntry, ServiceAvailabilityDay


class ServiceCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "enterprise_id": "550e8400-e29b-41d4-a716-446655440000",
                "service_name": "Kids Fitness Program",
                "service_description": "Weekly fitness classes for children",
                "service_category": "Fitness",
                "service_type": "group_class",
                "banner_image": "https://cdn.example.com/services/kids-fitness.png",
                "service_price": 150.0,
                "duration": 60,
                "availability_status": True,
                "service_status": True,
                "max_participants": 12,
                "provider_name": "Spin Health Co",
                "instructor_name": "Coach Alex",
                "delivery_format": "in_person",
                "package_price": 500.0,
                "currency": "USD",
                "cancellation_policy": "24-hour cancellation required",
                "availability_schedule": [
                    {
                        "day": "monday",
                        "is_available": True,
                        "start_time": "09:00",
                        "end_time": "17:00",
                        "slot_length": "60",
                    }
                ],
            }
        }
    )

    enterprise_id: UUID = Field(..., description="Enterprise ID")
    service_name: str = Field(..., description="Service name")
    service_description: str | None = None
    service_category: str = Field(..., description="Service category")
    service_type: str | None = Field(
        None,
        description="Service type label (e.g. group_class, personal_training).",
    )
    banner_image: str | None = Field(
        None,
        description="URL of the service banner image.",
    )
    service_price: float = Field(..., description="Service price")
    duration: int = Field(..., description="Duration in minutes")
    availability_status: bool = Field(default=True, description="Availability flag")
    service_status: bool = Field(default=True, description="Active/inactive flag")
    max_participants: int | None = None
    provider_name: str | None = None
    instructor_name: str | None = None
    delivery_format: str | None = None
    package_price: float | None = None
    currency: str | None = Field("USD", description="ISO currency code")
    cancellation_policy: str | None = None
    availability_schedule: list[AvailabilityScheduleEntry] | None = None


class ServiceUpdate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "service_type": "group_class",
                "banner_image": "https://cdn.example.com/services/kids-fitness.png",
                "max_participants": 12,
                "provider_name": "Spin Health Co",
                "instructor_name": "Coach Alex",
                "delivery_format": "in_person",
                "package_price": 500.0,
                "currency": "USD",
                "cancellation_policy": "24-hour cancellation required",
                "availability_schedule": [
                    {
                        "day": "monday",
                        "is_available": True,
                        "start_time": "09:00",
                        "end_time": "17:00",
                        "slot_length": "60",
                    }
                ],
            }
        }
    )

    service_name: str | None = None
    service_description: str | None = None
    service_category: str | None = None
    service_type: str | None = None
    banner_image: str | None = None
    service_price: float | None = None
    duration: int | None = None
    availability_status: bool | None = None
    service_status: bool | None = None
    max_participants: int | None = None
    provider_name: str | None = None
    instructor_name: str | None = None
    delivery_format: str | None = None
    package_price: float | None = None
    currency: str | None = None
    cancellation_policy: str | None = None
    availability_schedule: list[AvailabilityScheduleEntry] | None = None


class ServiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    enterprise_id: UUID
    service_name: str
    service_description: str | None
    service_category: str
    service_type: str | None = None
    banner_image: str | None = None
    service_price: float
    duration: int
    availability_status: bool
    service_status: bool
    max_participants: int | None
    provider_name: str | None
    instructor_name: str | None
    delivery_format: str | None
    package_price: float | None
    currency: str | None
    cancellation_policy: str | None
    availability_schedule: list[AvailabilityScheduleEntry] | None = None
    created_at: datetime | None = None


class ServiceListItemResponse(ServiceResponse):
    trainer_name: str | None = Field(
        None,
        description="Instructor name (alias of instructor_name).",
    )


_SERVICE_DETAIL_EXAMPLE = {
    "id": "550e8400-e29b-41d4-a716-446655440001",
    "enterprise_id": "550e8400-e29b-41d4-a716-446655440000",
    "enterprise_name": "Spin Health",
    "service_name": "Kids Fitness Program",
    "service_category": "Fitness",
    "type": "group_class",
    "banner_image": "https://cdn.example.com/services/kids-fitness.png",
    "trainer_name": "Coach Alex",
    "availability": [
        {
            "day": "Monday",
            "date": "2026-06-22",
            "slots": ["09:00-10:00", "10:00-11:00"],
        }
    ],
}


class ServiceDetailResponse(ServiceResponse):
    model_config = ConfigDict(
        json_schema_extra={"example": _SERVICE_DETAIL_EXAMPLE},
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
