from uuid import UUID
from typing import Optional

from pydantic import BaseModel
from pydantic import Field


class ServiceCreate(BaseModel):
    enterprise_id: UUID
    service_name: str
    service_description: Optional[str] = None
    service_category: str
    service_price: float = Field(gt=0)
    duration: int = Field(gt=0)
    availability_status: bool = True


class ServiceUpdate(BaseModel):
    service_name: Optional[str] = None
    service_description: Optional[str] = None
    service_category: Optional[str] = None
    service_price: Optional[float] = None
    duration: Optional[int] = None
    availability_status: Optional[bool] = None
    service_status: Optional[bool] = None


class ServiceResponse(BaseModel):
    id: UUID
    enterprise_id: UUID
    service_name: str
    service_description: Optional[str]
    service_category: str
    service_price: float
    duration: int
    availability_status: bool
    service_status: bool

    class Config:
        from_attributes = True