# v2 learning copy of app/api/v1/endpoints/admin.py: the Training, Course and Program approval routes.
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from mskit.auth.dependencies import extract_access_token, get_current_super_admin
from learning_service.db.database import get_db
from learning_service.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from learning_service.services.training_service import get_trainings_service, get_training_service, update_training_status_service
from learning_service.schemas.training_schema import TrainingAdminActionRequest, TrainingDetailResponse
from learning_service.services.program_service import get_programs_service, get_program_service, update_program_status_service
from learning_service.schemas.program_schema import ProgramAdminActionRequest, ProgramDetailResponse

router = APIRouter(tags=["Admin — Approvals"])



# registration_status is an attendee flag for the public lists; a management queue does not carry it.





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
    return update_program_status_service(db, program_id, "rejected", _admin, notes=reason, access_token=extract_access_token(request))

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







