from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class EventMediaField(str, Enum):
    primary_image = "primary_image"
    gallery_images = "gallery_images"
    videos = "videos"
    documents = "documents"


class EventMediaAssetResponse(BaseModel):
    """An uploaded file. Put `url` into the Event create/update JSON (documents: the whole
    `{id,url,name,size,type}` object, or just the url — the server fills in the rest)."""

    model_config = ConfigDict(json_schema_extra={"example": {
        "id": "7d2c1c3e-0b0f-4f3b-9d6e-5f6a0c8c9a11",
        "field": "documents",
        "url": "https://chat.wisdomtooth.tech/api/v1/events/media/7d2c1c3e-0b0f-4f3b-9d6e-5f6a0c8c9a11.pdf",
        "name": "Agenda.pdf",
        "size": 482113,
        "type": "application/pdf",
        "attached": False,
        "expires_at": "2026-10-07T10:15:00",
    }})

    id: str = Field(..., description="Asset id. Also the file name stem in `url`.")
    field: EventMediaField = Field(..., description="The Event field this file was uploaded for.")
    url: str = Field(..., description="Hosted https URL. Store this in the Event.")
    name: str = Field(..., description="Original file name (shown to attendees, used as the download name for documents).")
    size: int = Field(..., description="Size in bytes.")
    type: str = Field(..., description="MIME type, verified against the file's contents.")
    attached: bool = Field(..., description="False until an Event create/update references the url.")
    expires_at: str | None = Field(None, description="While `attached` is false: when the file is deleted if still unused (24 h after upload).")


class EventMediaDeleteResponse(BaseModel):
    deleted: bool = True
