from uuid import UUID
from typing import Optional

from pydantic import BaseModel
from pydantic import Field


class ProductCreate(BaseModel):
    enterprise_id: UUID
    product_name: str
    product_description: Optional[str] = None
    product_category: str
    product_price: float = Field(gt=0)
    product_images: Optional[str] = None


class ProductUpdate(BaseModel):
    product_name: Optional[str] = None
    product_description: Optional[str] = None
    product_category: Optional[str] = None
    product_price: Optional[float] = None
    product_images: Optional[str] = None
    product_status: Optional[bool] = None


class ProductResponse(BaseModel):
    id: UUID
    enterprise_id: UUID
    product_name: str
    product_description: Optional[str]
    product_category: str
    product_price: float
    product_images: Optional[str]
    product_status: bool

    class Config:
        from_attributes = True