from uuid import UUID

from pydantic import BaseModel
from pydantic import Field


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
        examples=[30 ]
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
    id: UUID

    enterprise_id: UUID

    service_name: str

    service_description: str | None

    service_category: str

    service_price: float

    duration: int

    availability_status: bool

    service_status: bool

    class Config:
        from_attributes = True