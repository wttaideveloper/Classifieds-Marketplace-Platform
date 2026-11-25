from datetime import datetime
from uuid import UUID

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common_schema import AttributeType, EntityStatus, EntityType, PaginatedResponse


class AttributeMedia(BaseModel):
    """One photo or video of an attribute. Upload the file first with POST /api/v1/products/upload and send the `url` it returns."""

    url: str = Field(..., max_length=1000, description="https URL (or our own /api/v1/... path). Base64 data URIs are refused.")
    type: Literal["image", "video"]
    name: str | None = Field(None, max_length=255)
    size: int | None = Field(None, ge=0)
    mime: str | None = Field(None, max_length=100)

    @field_validator("url")
    @classmethod
    def _hosted_url(cls, value: str) -> str:
        url = value.strip()
        if url.lower().startswith("data:"):
            raise ValueError("send a hosted URL, not base64: upload the file with POST /api/v1/products/upload first")
        if not (url.lower().startswith(("https://", "http://")) or url.startswith("/api/v1/")):
            raise ValueError("url must be an http(s) URL")
        return url


def _absolute(media):
    """Our own relative paths become absolute https URLs, so every client can load them."""
    if not media:
        return []
    from app.utils.public_urls import public_media_base, to_https

    base = public_media_base()
    out = []
    for item in media:
        item = dict(item) if isinstance(item, dict) else item.model_dump()
        url = str(item.get("url") or "")
        if url.startswith("/") and base:
            url = f"{base}{url}"
        item["url"] = to_https(url)
        out.append(item)
    return out


class AttributeCreate(BaseModel):
    tenant_id: UUID | None = Field(
        None,
        description="Tenant identifier for multi-tenant support.",
    )
    entity_type: EntityType = Field(
        ...,
        description="Entity type (enterprise, product, service)",
        examples=["enterprise"],
    )
    entity_id: UUID = Field(
        ...,
        description="Entity UUID",
    )
    attribute_name: str = Field(
        ...,
        description="Attribute name",
        examples=["License Number"],
    )
    attribute_value: str = Field(
        ...,
        description="Attribute value",
        examples=["LIC-12345"],
    )
    attribute_type: AttributeType = Field(
        ...,
        description="Attribute type",
        examples=["text"],
    )
    is_required: bool = Field(False, description="Whether the attribute is required.")
    status: EntityStatus = Field("active", description="Attribute lifecycle status.")
    media: list[AttributeMedia] | None = Field(None, max_length=20, description="Photos and videos of this attribute (at most 20).")


class AttributeUpdate(BaseModel):
    attribute_name: str | None = Field(
        None,
        description="Updated attribute name",
        examples=["GST Number"],
    )
    attribute_value: str | None = Field(
        None,
        description="Updated attribute value",
        examples=["GST123456789"],
    )
    attribute_type: AttributeType | None = Field(
        None,
        description="Updated attribute type",
        examples=["text"],
    )
    is_required: bool | None = None
    status: EntityStatus | None = None
    media: list[AttributeMedia] | None = Field(None, max_length=20, description="Replaces the whole list; [] removes every photo and video.")


class AttributeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None = None
    entity_type: EntityType
    entity_id: UUID
    attribute_name: str
    attribute_value: str
    attribute_type: AttributeType
    is_required: bool
    status: EntityStatus
    media: list[AttributeMedia] = Field(default_factory=list, description="Photos and videos, with absolute https URLs.")
    created_at: datetime | None = None

    @field_validator("media", mode="before")
    @classmethod
    def _media(cls, value):
        return _absolute(value)


class AttributePaginatedResponse(PaginatedResponse[AttributeResponse]):
    pass
