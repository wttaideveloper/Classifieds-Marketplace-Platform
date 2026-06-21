from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common_schema import AvailabilityResponse, AvailabilityScheduleEntry


class ServiceCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "enterprise_id": "550e8400-e29b-41d4-a716-446655440000",
                "service_name": "Kids Fitness Program",
                "service_description": "Weekly fitness classes for children",
                "service_category": "Fitness",
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

    service_price: float = Field(..., description="Service price")

    duration: int = Field(..., description="Duration in minutes")

    availability_status: bool = Field(default=True, description="Availability flag")

    service_status: bool = Field(default=True, description="Active/inactive flag")

    max_participants: int | None = Field(
        None,
        description="Maximum participants per session",
    )

    provider_name: str | None = Field(None, description="Service provider name")

    instructor_name: str | None = Field(None, description="Instructor or trainer name")

    delivery_format: str | None = Field(
        None,
        description="Delivery format (e.g. in_person, online, hybrid)",
    )

    package_price: float | None = Field(None, description="Package or bundle price")

    currency: str | None = Field("USD", description="ISO currency code")

    cancellation_policy: str | None = Field(None, description="Cancellation policy text")

    availability_schedule: list[AvailabilityScheduleEntry] | None = Field(
        None,
        description="Weekly availability schedule",
    )


class ServiceUpdate(BaseModel):
    service_name: str | None = None
    service_description: str | None = None
    service_category: str | None = None
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


class ServiceDetailResponse(ServiceResponse):
    trainer_name: str | None = Field(
        None,
        description="Instructor name (alias of instructor_name).",
    )
    format: str | None = Field(
        None,
        description="Delivery format (alias of delivery_format).",
    )
    availability: AvailabilityResponse = Field(
        default_factory=AvailabilityResponse,
        description="Derived availability summary from availability_schedule.",
    )
