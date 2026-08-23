from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common_schema import PaginatedResponse


TrainingStatus = str  # draft|published|unpublished|archived|cancelled


class TrainingCreate(BaseModel):
    tenant_id: UUID | None = None
    enterprise_id: UUID = Field(..., description="Enterprise ID")
    location_id: UUID | None = None
    title: str
    description: str | None = None
    category: str = Field(..., description="Category")
    subcategory: str | None = None
    tags: list[str] | None = None
    instructor_id: UUID | None = None
    requirements: str | None = None
    primary_image: str | None = None
    gallery_images: list | None = None
    promotional_video: str | None = None
    documents: list | None = None
    delivery_mode: str | None = Field("self_paced", description="self_paced|instructor_led|blended")
    course_type: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    enrolment_start: datetime | None = None
    enrolment_end: datetime | None = None
    time_zone: str | None = "Asia/Kolkata"
    capacity: str | None = None
    price: str | None = None
    currency: str | None = "INR"
    promo_price: str | None = None
    status: TrainingStatus = Field("draft")

    def to_model_data(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "enterprise_id": self.enterprise_id,
            "location_id": self.location_id,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "subcategory": self.subcategory,
            "tags": self.tags or [],
            "instructor_id": self.instructor_id,
            "requirements": self.requirements,
            "primary_image": self.primary_image,
            "gallery_images": self.gallery_images or [],
            "promotional_video": self.promotional_video,
            "documents": self.documents or [],
            "delivery_mode": self.delivery_mode,
            "course_type": self.course_type,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "enrolment_start": self.enrolment_start,
            "enrolment_end": self.enrolment_end,
            "time_zone": self.time_zone,
            "capacity": self.capacity,
            "price": self.price,
            "currency": self.currency,
            "promo_price": self.promo_price,
            "status": self.status,
        }


class TrainingUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    category: str | None = None
    subcategory: str | None = None
    tags: list | None = None
    instructor_id: UUID | None = None
    requirements: str | None = None
    primary_image: str | None = None
    gallery_images: list | None = None
    promotional_video: str | None = None
    documents: list | None = None
    delivery_mode: str | None = None
    course_type: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    enrolment_start: datetime | None = None
    enrolment_end: datetime | None = None
    time_zone: str | None = None
    capacity: str | None = None
    price: str | None = None
    currency: str | None = None
    promo_price: str | None = None
    status: TrainingStatus | None = None

    def to_model_data(self) -> dict:
        return self.model_dump(exclude_unset=True)


class TrainingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID | None = None
    enterprise_id: UUID
    location_id: UUID | None = None
    title: str
    description: str | None = None
    category: str
    subcategory: str | None = None
    tags: list | None = None
    instructor_id: UUID | None = None
    delivery_mode: str | None = None
    course_type: str | None = None
    capacity: str | None = None
    price: str | None = None
    currency: str | None = None
    status: str
    is_deleted: bool | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    sections: list | None = None
    assessments: list | None = None


class TrainingListItemResponse(TrainingResponse):
    pass


class TrainingDetailResponse(TrainingResponse):
    enterprise_name: str | None = None
    model_config = ConfigDict(from_attributes=True)


class TrainingPaginatedResponse(PaginatedResponse[TrainingListItemResponse]):
    pass


class TrainingStatusUpdate(BaseModel):
    status: str
    reason: str | None = None


class SectionCreate(BaseModel):
    title: str
    order: int | None = 0


class LessonCreate(BaseModel):
    type: str = Field("text", description="text|video|audio|webpage|pdf|live")
    title: str
    content_url: str | None = None
    duration: int | None = None
    is_preview: bool | None = False
    is_draft: bool | None = False
    prerequisites: list | None = None
    release_rule: dict | None = None
