"""Internal API of the moderation service: POST /internal/v1/review-log (review owners record moderation history)."""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from moderation_service.db.database import get_db
from moderation_service.models.review_audit_model import ReviewModerationLog
from mskit.app import require_internal_key

router = APIRouter(dependencies=[Depends(require_internal_key)], tags=["internal"])


class LogEntry(BaseModel):
    module: str
    review_id: UUID
    item_id: UUID
    item_name: str | None = None
    tenant_id: UUID | None = None
    from_status: str | None = None
    to_status: str
    actor_user_id: UUID | None = None
    actor_role: str | None = None
    actor_name: str | None = None
    customer_name: str | None = None
    business_name: str | None = None
    reason: str | None = None


@router.post("/review-log", status_code=201)
def review_log(entry: LogEntry, db: Session = Depends(get_db)):
    db.add(ReviewModerationLog(**entry.model_dump()))
    db.commit()
    return {"recorded": True}
