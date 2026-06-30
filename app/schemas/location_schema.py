from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.common_schema import LocationStatus, PaginatedResponse


class LocationCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "location_name": "Downtown Studio",
                "address_line_1": "123 Main St",
                "city": "Austin",
                "state": "TX",
                "country": "USA",
                "postal_code": "78701",
                "phone": "+1-555-0200",
                "email": "downtown@spinhealth.com",
                "latitude": 30.2672,
                "longitude": -97.7431,
                "status": "active",
            }
        }
    )

    location_name: str = Field(..., description="Display name for the location.")
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    latitude: float | None = None
    longitude: float | None = None
    status: LocationStatus = Field("active", description="Location lifecycle status.")


class LocationUpdate(BaseModel):
    location_name: str | None = None
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    latitude: float | None = None
    longitude: float | None = None
    status: LocationStatus | None = None


class LocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    enterprise_id: UUID
    location_name: str
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    email: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    status: LocationStatus
    created_at: datetime | None = None


class LocationPaginatedResponse(PaginatedResponse[LocationResponse]):
    pass
