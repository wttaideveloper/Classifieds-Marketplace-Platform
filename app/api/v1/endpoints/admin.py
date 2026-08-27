from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.core.dependencies import get_current_admin
from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.services.event_service import get_events_service, update_event_status_service

router = APIRouter(tags=["Admin — Approvals"])

@router.get("/events/pending", summary="Admin — Pending Events Queue")
def admin_pending_events(
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    enterprise_id: UUID | None = Query(None),
    category: str | None = Query(None),
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_admin),
):
    return get_events_service(db, status_filter="pending_approval", enterprise_id=enterprise_id, category=category, page=page, page_size=page_size)

@router.post("/events/{event_id}/approve", summary="Admin — Approve Event")
def approve_event(event_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_admin)):
    return update_event_status_service(db, event_id, "approved")

@router.post("/events/{event_id}/reject", summary="Admin — Reject Event")
def reject_event(event_id: UUID, payload: dict | None = None, db: Session = Depends(get_db), _admin: dict = Depends(get_current_admin)):
    # rejection maps to cancelled with optional reason ignored for now (persisted in notification metadata)
    return update_event_status_service(db, event_id, "cancelled")

@router.post("/events/{event_id}/publish", summary="Admin — Publish Approved Event")
def publish_event(event_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_admin)):
    return update_event_status_service(db, event_id, "published")
