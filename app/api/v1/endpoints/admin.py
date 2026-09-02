from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.core.dependencies import get_current_super_admin
from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.services.event_service import get_events_service, update_event_status_service
from app.services.training_service import get_trainings_service, update_training_status_service
from app.services.program_service import get_programs_service, update_program_status_service
from app.models.event_aux_models import EventCategory

router = APIRouter(tags=["Admin — Approvals"])

@router.get("/events/pending", summary="Admin — Pending Events Queue")
def admin_pending_events(
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    enterprise_id: UUID | None = Query(None),
    category: str | None = Query(None),
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_super_admin),
):
    return get_events_service(db, status_filter="pending_approval", enterprise_id=enterprise_id, category=category, page=page, page_size=page_size)

@router.post("/events/{event_id}/approve", summary="Admin — Approve Event")
def approve_event(event_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return update_event_status_service(db, event_id, "approved", _admin)

@router.post("/events/{event_id}/reject", summary="Admin — Reject Event")
def reject_event(
    event_id: UUID,
    payload: dict | None = None,
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_super_admin),
):
    from app.schemas.event_schema import EventAdminActionRequest
    reason = None
    if payload:
        try:
            body = EventAdminActionRequest(**payload)
            reason = body.reason
        except Exception:
            reason = payload.get("reason") or payload.get("message") or str(payload)
    return update_event_status_service(db, event_id, "rejected", _admin, notes=reason)

@router.post("/events/{event_id}/request-changes", summary="Admin — Request Changes on Event")
def request_changes_event(
    event_id: UUID,
    payload: dict | None = None,
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_super_admin),
):
    from app.schemas.event_schema import EventAdminActionRequest
    reason = None
    if payload:
        try:
            body = EventAdminActionRequest(**payload)
            reason = body.reason
        except Exception:
            reason = payload.get("reason") or payload.get("message") or str(payload)
    if not reason:
        from fastapi import HTTPException as _HE
        raise _HE(status_code=400, detail="reason is required for requesting changes")
    return update_event_status_service(db, event_id, "needs_revision", _admin, notes=reason)

@router.post("/events/{event_id}/publish", summary="Admin — Publish Approved Event")
def publish_event(event_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return update_event_status_service(db, event_id, "published", _admin)

# Trainings admin queue (same flow: draft -> pending_approval -> approved -> published)
@router.get("/trainings/pending", summary="Admin — Pending Trainings Queue")
def admin_pending_trainings(page: int = Query(DEFAULT_PAGE, ge=1), page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE), enterprise_id: UUID | None = Query(None), category: str | None = Query(None), db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return get_trainings_service(db, status="pending_approval", enterprise_id=enterprise_id, category=category, page=page, page_size=page_size)

@router.post("/trainings/{training_id}/approve", summary="Admin — Approve Training")
def approve_training(training_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return update_training_status_service(db, training_id, "approved")

@router.post("/trainings/{training_id}/reject", summary="Admin — Reject Training")
def reject_training(training_id: UUID, payload: dict | None = None, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return update_training_status_service(db, training_id, "cancelled")

@router.post("/trainings/{training_id}/publish", summary="Admin — Publish Approved Training")
def publish_training(training_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return update_training_status_service(db, training_id, "published")

# Programs admin queue
@router.get("/programs/pending", summary="Admin — Pending Programs Queue")
def admin_pending_programs(page: int = Query(DEFAULT_PAGE, ge=1), page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE), enterprise_id: UUID | None = Query(None), category: str | None = Query(None), db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return get_programs_service(db, status="pending_approval", enterprise_id=enterprise_id, category=category, page=page, page_size=page_size)

@router.post("/programs/{program_id}/approve", summary="Admin — Approve Program")
def approve_program(program_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return update_program_status_service(db, program_id, "approved")

@router.post("/programs/{program_id}/reject", summary="Admin — Reject Program")
def reject_program(program_id: UUID, payload: dict | None = None, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return update_program_status_service(db, program_id, "cancelled")

@router.post("/programs/{program_id}/publish", summary="Admin — Publish Approved Program")
def publish_program(program_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return update_program_status_service(db, program_id, "published")

# Event Categories — Admin-managed
@router.get("/event-categories", summary="Admin — List Event Categories")
def list_categories(db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return db.query(EventCategory).order_by(EventCategory.name).all()

@router.post("/event-categories", status_code=201, summary="Admin — Create Event Category")
def create_category(payload: dict, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    from fastapi import HTTPException
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    exists = db.query(EventCategory).filter(EventCategory.name == name).first()
    if exists:
        raise HTTPException(status_code=400, detail="Category already exists")
    parent_id = payload.get("parent_id")
    cat = EventCategory(name=name, parent_id=parent_id, description=payload.get("description"))
    db.add(cat); db.commit(); db.refresh(cat)
    return cat

@router.delete("/event-categories/{category_id}", summary="Admin — Delete Event Category")
def delete_category(category_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    from fastapi import HTTPException
    cat = db.query(EventCategory).filter(EventCategory.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    db.delete(cat); db.commit()
    return {"message": "Category deleted"}

@router.get("/event-audits/{event_id}", summary="Admin — Event Audit History")
def event_audits(event_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    from app.models.event_aux_models import EventAudit
    audits = db.query(EventAudit).filter(EventAudit.event_id == event_id).order_by(EventAudit.created_at.desc()).all()
    return [
        {
            "id": str(a.id),
            "event_id": str(a.event_id),
            "changed_by": a.changed_by,
            "action": a.action,
            "before": a.before,
            "after": a.after,
            "notes": a.notes,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in audits
    ]
