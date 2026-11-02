from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common_schema import PaginatedResponse


class GovernanceModules(BaseModel):
    events: bool = True
    trainings: bool = True
    products: bool = True
    services: bool = True
    general: bool = True


class GovernanceActor(BaseModel):
    id: str
    name: str | None = None


class GovernanceOut(BaseModel):
    modules: GovernanceModules
    updated_at: datetime | None = Field(None, description="When any setting last really changed; null while nothing was ever changed")
    updated_by: GovernanceActor | None = None


class GovernanceUpdate(BaseModel):
    enabled: bool


class GovernanceAuditItem(BaseModel):
    id: UUID
    module: str
    previous_value: bool
    new_value: bool
    action: str = Field(..., description="enable | disable | reset")
    actor_id: str | None = None
    actor_name: str | None = None
    actor_role: str | None = None
    created_at: datetime


class GovernanceAuditPage(PaginatedResponse[GovernanceAuditItem]):
    pass
