from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.core.dependencies import get_current_admin
from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.services.event_service import get_events_service, update_event_status_service
from app.services.training_service import get_trainings_service, update_training_status_service
from app.services.program_service import get_programs_service, update_program_status_service

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
    return update_event_status_service(db, event_id, "cancelled")

@router.post("/events/{event_id}/publish", summary="Admin — Publish Approved Event")
def publish_event(event_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_admin)):
    return update_event_status_service(db, event_id, "published")

# Trainings admin queue (same flow: draft -> pending_approval -> approved -> published)
@router.get("/trainings/pending", summary="Admin — Pending Trainings Queue")
def admin_pending_trainings(page: int = Query(DEFAULT_PAGE, ge=1), page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE), enterprise_id: UUID | None = Query(None), category: str | None = Query(None), db: Session = Depends(get_db), _admin: dict = Depends(get_current_admin)):
    return get_trainings_service(db, status="pending_approval", enterprise_id=enterprise_id, category=category, page=page, page_size=page_size)

@router.post("/trainings/{training_id}/approve", summary="Admin — Approve Training")
def approve_training(training_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_admin)):
    return update_training_status_service(db, training_id, "approved")

@router.post("/trainings/{training_id}/reject", summary="Admin — Reject Training")
def reject_training(training_id: UUID, payload: dict | None = None, db: Session = Depends(get_db), _admin: dict = Depends(get_current_admin)):
    return update_training_status_service(db, training_id, "cancelled")

@router.post("/trainings/{training_id}/publish", summary="Admin — Publish Approved Training")
def publish_training(training_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_admin)):
    return update_training_status_service(db, training_id, "published")

# Programs admin queue
@router.get("/programs/pending", summary="Admin — Pending Programs Queue")
def admin_pending_programs(page: int = Query(DEFAULT_PAGE, ge=1), page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE), enterprise_id: UUID | None = Query(None), category: str | None = Query(None), db: Session = Depends(get_db), _admin: dict = Depends(get_current_admin)):
    return get_programs_service(db, status="pending_approval", enterprise_id=enterprise_id, category=category, page=page, page_size=page_size)

@router.post("/programs/{program_id}/approve", summary="Admin — Approve Program")
def approve_program(program_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_admin)):
    return update_program_status_service(db, program_id, "approved")

@router.post("/programs/{program_id}/reject", summary="Admin — Reject Program")
def reject_program(program_id: UUID, payload: dict | None = None, db: Session = Depends(get_db), _admin: dict = Depends(get_current_admin)):
    return update_program_status_service(db, program_id, "cancelled")

@router.post("/programs/{program_id}/publish", summary="Admin — Publish Approved Program")
def publish_program(program_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_admin)):
    return update_program_status_service(db, program_id, "published")
