from uuid import UUID
from datetime import datetime
from pydantic import BaseModel

class MediaResponse(BaseModel):
    id: UUID
    file_name: str
    original_file_name: str
    file_type: str
    mime_type: str
    file_size: int
    file_url: str
    created_at: datetime

    class Config:
        from_attributes = True

class UploadResponse(BaseModel):
    success: bool
    message: str
    data: list[MediaResponse]

class GetMediaResponse(BaseModel):
    success: bool
    message: str
    data: MediaResponse

class DeleteMediaResponse(BaseModel):
    success: bool
    message: str

class BusinessGalleryMediaResponse(BaseModel):
    id: UUID
    business_id: UUID
    file_name: str
    original_file_name: str
    file_type: str
    mime_type: str
    file_size: int
    file_url: str
    created_at: datetime

    class Config:
        from_attributes = True


class BusinessGalleryUploadResponse(BaseModel):
    success: bool
    message: str
    data: list[BusinessGalleryMediaResponse]

class ListingMediaResponse(BaseModel):
    id: UUID
    listing_id: UUID
    file_name: str
    original_file_name: str
    file_type: str
    mime_type: str
    file_size: int
    file_url: str
    created_at: datetime

    class Config:
        from_attributes = True


class ListingMediaUploadResponse(BaseModel):
    success: bool
    message: str
    data: list[ListingMediaResponse]