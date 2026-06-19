from uuid import UUID
from pydantic import BaseModel, EmailStr, Field


class EnterpriseCreate(BaseModel):
    business_short_name: str = Field(
        ...,
        description="Business short name",
        examples=["DEF"]
    )

    business_legal_name: str = Field(
        ...,
        description="Business legal name",
        examples=["DEF Technologies Pvt Ltd"]
    )

    business_description: str | None = Field(
        None,
        description="Business description"
    )

    business_email: EmailStr

    business_phone: str | None = None

    registered_address: str | None = None

    business_address: str | None = None

    communication_address: str | None = None

    logo_url: str | None = None

    business_images: str | None = None


class EnterpriseUpdate(BaseModel):
    business_short_name: str | None = None

    business_legal_name: str | None = None

    business_description: str | None = None

    business_email: EmailStr | None = None

    business_phone: str | None = None

    registered_address: str | None = None

    business_address: str | None = None

    communication_address: str | None = None

    logo_url: str | None = None

    business_images: str | None = None

    status: bool | None = None


class EnterpriseResponse(BaseModel):
    id: UUID

    business_short_name: str

    business_legal_name: str

    business_description: str | None

    business_email: str

    business_phone: str | None

    registered_address: str | None

    business_address: str | None

    communication_address: str | None

    logo_url: str | None

    business_images: str | None

    status: bool

    class Config:
        from_attributes = True