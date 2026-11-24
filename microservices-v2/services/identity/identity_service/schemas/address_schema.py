from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AddressCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "label": "Home",
                "full_name": "Suresh Inti",
                "phone": "+1 415 555 0198",
                "line1": "2214 SE Ash St",
                "line2": "Apt 2",
                "city": "Portland",
                "state": "OR",
                "zip": "97214",
                "country": "United States",
                "is_default": False,
            }
        }
    )

    label: str | None = Field(None, max_length=50, description="Address label, e.g. Home/Work")
    full_name: str = Field(..., min_length=1, max_length=120, description="Recipient full name")
    phone: str = Field(..., min_length=7, max_length=20, description="Contact phone")
    line1: str = Field(..., min_length=1, max_length=255, description="Address line 1")
    line2: str | None = Field(None, max_length=255, description="Address line 2")
    city: str = Field(..., min_length=1, max_length=100)
    state: str = Field(..., min_length=1, max_length=100)
    zip: str = Field(..., min_length=1, max_length=20, description="ZIP / postal code")
    country: str = Field(..., min_length=1, max_length=100)
    is_default: bool = Field(False, description="Set as the default address; unsets any other default.")


class AddressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    label: str | None = None
    full_name: str
    phone: str
    line1: str
    line2: str | None = None
    city: str
    state: str
    zip: str
    country: str
    is_default: bool


class AddressListResponse(BaseModel):
    items: list[AddressResponse] = Field(default_factory=list)
