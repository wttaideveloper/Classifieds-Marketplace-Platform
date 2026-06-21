from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common_schema import AvailabilityResponse


class ServiceCreate(BaseModel):
    enterprise_id: UUID = Field(
        ...,
        description="Enterprise ID"
    )

    service_name: str = Field(
        ...,
        description="Service Name",
        examples=["Website Development"]
    )

    service_description: str | None = Field(
        None,
        description="Service Description",
        examples=["Custom website development services"]
    )

    service_category: str = Field(
        ...,
        description="Service Category",
        examples=["IT Services"]
    )

    service_price: float = Field(
        ...,
        description="Service Price",
        examples=[15000.00]
    )

    duration: int = Field(
        ...,
        description="Service Duration",
        examples=[30]
    )

    availability_status: bool = Field(
        default=True,
        description="Availability Status"
    )

    service_status: bool = Field(
        default=True,
        description="Service Status"
    )


class ServiceUpdate(BaseModel):
    service_name: str | None = Field(
        None,
        description="Updated Service Name"
    )

    service_description: str | None = Field(
        None,
        description="Updated Service Description"
    )

    service_category: str | None = Field(
        None,
        description="Updated Service Category"
    )

    service_price: float | None = Field(
        None,
        description="Updated Service Price"
    )

    duration: int | None = Field(
        None,
        description="Updated Duration"
    )

    availability_status: bool | None = Field(
        None,
        description="Updated Availability Status"
    )

    service_status: bool | None = Field(
        None,
        description="Updated Service Status"
    )


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


class ServiceListItemResponse(ServiceResponse):
    trainer_name: str | None = Field(
        None,
        description="Assigned trainer name (placeholder until stored in database).",
    )


class ServiceDetailResponse(ServiceResponse):
    banner_image: str | None = Field(
        None,
        description="Service banner image URL (placeholder until stored in database).",
    )
    trainer_name: str | None = Field(
        None,
        description="Assigned trainer name (placeholder until stored in database).",
    )
    expertise_name: str | None = Field(
        None,
        description="Trainer expertise (placeholder until stored in database).",
    )
    type: str | None = Field(
        None,
        description="Service type (placeholder until stored in database).",
    )
    format: str | None = Field(
        None,
        description="Service format (placeholder until stored in database).",
    )
    availability: AvailabilityResponse = Field(
        default_factory=AvailabilityResponse,
        description="Weekly availability slots (placeholder until stored in database).",
    )
