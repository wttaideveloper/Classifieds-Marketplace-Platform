from pydantic import BaseModel
from typing import List

class HealthResponse(BaseModel):
    status: str
    database: str
    message: str

class InventoryItem(BaseModel):
    method: str
    endpoint: str
    description: str

class InventoryResponse(BaseModel):
    total_apis: int
    apis: List[InventoryItem]