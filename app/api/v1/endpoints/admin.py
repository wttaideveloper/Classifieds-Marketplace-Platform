from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from app.core.dependencies import extract_access_token, get_current_super_admin
from app.db.database import get_db
from app.repository.event_repo import build_event_viewer, is_platform_super_admin, require_event_owner
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.services.event_service import get_events_service, update_event_status_service
from app.services.training_service import get_trainings_service, get_training_service, update_training_status_service
from app.schemas.training_schema import TrainingAdminActionRequest, TrainingDetailResponse
from app.services.program_service import get_programs_service, get_program_service, update_program_status_service
from app.schemas.program_schema import ProgramAdminActionRequest, ProgramDetailResponse
from app.models.event_aux_models import EventCategory

router = APIRouter(tags=["Admin — Approvals"])


def _scope_event_admin_action(request: Request, db: Session, event_id: UUID, admin_user: dict) -> None:
    """Tenant-scope the Event routes of this router.

    ``get_current_super_admin`` also admits an Enterprise Admin (role "admin") as a backwards-compatible
    testing fallback; that policy is unchanged here and still applies to trainings/programs/blogs.
    For Events, however, that fallback identity is tenant-scoped: only an active Platform Super Admin
    may approve / reject / publish / read the audit of another tenant's event.
    """
    if not is_platform_super_admin(admin_user):
        require_event_owner(db, event_id, admin_user, access_token=extract_access_token(request), include_deleted=True)

@router.get("/events/pending", summary="Admin — Pending Events Queue")
def admin_pending_events(
    request: Request,
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    enterprise_id: UUID | None = Query(None),
    category: str | None = Query(None),
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_super_admin),
):
    # Platform Super Admin sees every tenant's queue; the Enterprise Admin fallback only its own.
    viewer = build_event_viewer(db, _admin, access_token=extract_access_token(request))
    return get_events_service(db, status_filter="pending_approval", enterprise_id=enterprise_id, category=category, page=page, page_size=page_size, viewer=viewer)

@router.post("/events/{event_id}/approve", summary="Admin — Approve Event")
def approve_event(request: Request, event_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    _scope_event_admin_action(request, db, event_id, _admin)
    return update_event_status_service(db, event_id, "approved", _admin, access_token=extract_access_token(request))

@router.post("/events/{event_id}/reject", summary="Admin — Reject Event")
def reject_event(
    request: Request,
    event_id: UUID,
    payload: dict | None = None,
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_super_admin),
):
    from app.schemas.event_schema import EventAdminActionRequest
    _scope_event_admin_action(request, db, event_id, _admin)
    reason = None
    if payload:
        try:
            body = EventAdminActionRequest(**payload)
            reason = body.reason
        except Exception:
            reason = payload.get("reason") or payload.get("message") or str(payload)
    return update_event_status_service(db, event_id, "rejected", _admin, notes=reason, access_token=extract_access_token(request))

@router.post("/events/{event_id}/request-changes", summary="Admin — Request Changes on Event")
def request_changes_event(
    request: Request,
    event_id: UUID,
    payload: dict | None = None,
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_super_admin),
):
    from app.schemas.event_schema import EventAdminActionRequest
    _scope_event_admin_action(request, db, event_id, _admin)
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
    return update_event_status_service(db, event_id, "needs_revision", _admin, notes=reason, access_token=extract_access_token(request))

@router.post("/events/{event_id}/publish", summary="Admin — Publish Approved Event")
def publish_event(request: Request, event_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    _scope_event_admin_action(request, db, event_id, _admin)
    return update_event_status_service(db, event_id, "published", _admin, access_token=extract_access_token(request))

# Trainings admin queue (same flow: draft -> pending_approval -> approved -> published)
@router.get("/trainings/pending", summary="Admin — Pending Trainings Queue")
def admin_pending_trainings(page: int = Query(DEFAULT_PAGE, ge=1), page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE), enterprise_id: UUID | None = Query(None), category: str | None = Query(None), db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return get_trainings_service(db, status="pending_approval", enterprise_id=enterprise_id, category=category, page=page, page_size=page_size)

@router.get("/trainings/{training_id}", response_model=TrainingDetailResponse, summary="Admin — Training detail for approval review")
def admin_get_training(training_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return get_training_service(db, training_id)

@router.post("/trainings/{training_id}/approve", summary="Admin — Approve Training")
def approve_training(request: Request, training_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return update_training_status_service(db, training_id, "approved", _admin, access_token=extract_access_token(request))

@router.post("/trainings/{training_id}/reject", summary="Admin — Reject Training")
def reject_training(
    request: Request,
    training_id: UUID,
    payload: TrainingAdminActionRequest | None = None,
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_super_admin),
):
    reason = payload.reason if payload else None
    return update_training_status_service(db, training_id, "rejected", _admin, notes=reason, access_token=extract_access_token(request))

@router.post("/trainings/{training_id}/request-changes", summary="Admin — Request Changes on Training")
def request_changes_training(
    request: Request,
    training_id: UUID,
    payload: TrainingAdminActionRequest,
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_super_admin),
):
    return update_training_status_service(db, training_id, "needs_revision", _admin, notes=payload.reason, access_token=extract_access_token(request))

@router.post("/trainings/{training_id}/publish", summary="Admin — Publish Approved Training")
def publish_training(request: Request, training_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return update_training_status_service(db, training_id, "published", _admin, access_token=extract_access_token(request))

# Courses admin queue — alias of the Trainings queue above. Same model, same
# table, same data; "Course" is just the frontend/product name for a Training.
@router.get("/courses/pending", summary="Admin — Pending Courses Queue")
def admin_pending_courses(page: int = Query(DEFAULT_PAGE, ge=1), page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE), enterprise_id: UUID | None = Query(None), category: str | None = Query(None), db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return get_trainings_service(db, status="pending_approval", enterprise_id=enterprise_id, category=category, page=page, page_size=page_size)

@router.get("/courses/{training_id}", response_model=TrainingDetailResponse, summary="Admin — Course detail for approval review")
def admin_get_course(training_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return get_training_service(db, training_id)

@router.post("/courses/{training_id}/approve", summary="Admin — Approve Course")
def approve_course(request: Request, training_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return update_training_status_service(db, training_id, "approved", _admin, access_token=extract_access_token(request))

@router.post("/courses/{training_id}/reject", summary="Admin — Reject Course")
def reject_course(
    request: Request,
    training_id: UUID,
    payload: TrainingAdminActionRequest | None = None,
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_super_admin),
):
    reason = payload.reason if payload else None
    return update_training_status_service(db, training_id, "rejected", _admin, notes=reason, access_token=extract_access_token(request))

@router.post("/courses/{training_id}/request-changes", summary="Admin — Request Changes on Course")
def request_changes_course(
    request: Request,
    training_id: UUID,
    payload: TrainingAdminActionRequest,
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_super_admin),
):
    return update_training_status_service(db, training_id, "needs_revision", _admin, notes=payload.reason, access_token=extract_access_token(request))

@router.post("/courses/{training_id}/publish", summary="Admin — Publish Approved Course")
def publish_course(request: Request, training_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return update_training_status_service(db, training_id, "published", _admin, access_token=extract_access_token(request))

# Programs admin queue
@router.get("/programs/pending", summary="Admin — Pending Programs Queue")
def admin_pending_programs(page: int = Query(DEFAULT_PAGE, ge=1), page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE), enterprise_id: UUID | None = Query(None), category: str | None = Query(None), db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return get_programs_service(db, status="pending_approval", enterprise_id=enterprise_id, category=category, page=page, page_size=page_size)

@router.get("/programs/{program_id}", response_model=ProgramDetailResponse, summary="Admin — Program detail for approval review")
def admin_get_program(program_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return get_program_service(db, program_id)

@router.post("/programs/{program_id}/approve", summary="Admin — Approve Program")
def approve_program(program_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return update_program_status_service(db, program_id, "approved", _admin)

@router.post("/programs/{program_id}/reject", summary="Admin — Reject Program")
def reject_program(
    program_id: UUID,
    payload: ProgramAdminActionRequest | None = None,
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_super_admin),
):
    reason = payload.reason if payload else None
    return update_program_status_service(db, program_id, "rejected", _admin, notes=reason)

@router.post("/programs/{program_id}/request-changes", summary="Admin — Request Changes on Program")
def request_changes_program(
    program_id: UUID,
    payload: ProgramAdminActionRequest,
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_super_admin),
):
    return update_program_status_service(db, program_id, "needs_revision", _admin, notes=payload.reason)

@router.post("/programs/{program_id}/publish", summary="Admin — Publish Approved Program")
def publish_program(program_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    return update_program_status_service(db, program_id, "published", _admin)

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
def event_audits(request: Request, event_id: UUID, db: Session = Depends(get_db), _admin: dict = Depends(get_current_super_admin)):
    from app.models.event_aux_models import EventAudit
    _scope_event_admin_action(request, db, event_id, _admin)
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


@router.get(
    "/notifications/diagnostics",
    summary="Admin — Why did (or didn't) a workflow notification reach someone",
    description=(
        "Platform Super Admin only. Shows what the notification recipient resolvers see right now: whether the "
        "Invigorate internal API is configured, which Platform Super Admin and owning Enterprise Admin user ids "
        "resolve (and why others were excluded), whether the realtime loop is attached, and — when `event_id` or "
        "`training_id` is given — the notification rows actually recorded for it and their recipients. "
        "Counts and ids only; no emails, names or secrets."
    ),
)
def notification_diagnostics(
    event_id: UUID | None = Query(None, description="Event whose approval notifications to inspect"),
    training_id: UUID | None = Query(None, description="Training whose approval/enrollment notifications to inspect"),
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_super_admin),
):
    from app.core.config import settings
    from app.models.event_model import Event
    from app.models.training_model import Training
    from app.realtime.loop_bridge import has_main_loop
    from app.services import notification_diagnostics as diag

    # get_current_super_admin also admits an Enterprise Admin fallback; this view lists admin ids, so it is stricter.
    if not is_platform_super_admin(_admin):
        raise HTTPException(status_code=403, detail="Platform Super Admin only")

    result = {
        "caller_user_id": str(_admin.get("id")),
        "config": {
            "invigorate_internal_api_configured": settings.invigorate_internal_api_configured,
            "socketio_redis_configured": bool(settings.SOCKETIO_REDIS_URL.strip()),
            "realtime_loop_attached": has_main_loop(),
        },
        "platform_admins": diag.diagnose_platform_admins(),
    }
    for kind, entity_id, model in (("event", event_id, Event), ("training", training_id, Training)):
        if entity_id is None:
            continue
        entity = db.get(model, entity_id)
        if entity is None:
            raise HTTPException(status_code=404, detail=f"{kind.title()} not found")
        result[kind] = {
            "id": str(entity.id),
            "status": entity.status,
            "enterprise_admins": diag.diagnose_enterprise_admins(db, entity),
            "recorded_notifications": diag.recorded_notifications(db, entity.id),
        }
    return result
