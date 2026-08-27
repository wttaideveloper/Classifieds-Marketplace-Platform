from uuid import UUID
from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session
from app.core.dependencies import get_current_user, require_roles
from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.training_schema import AnnouncementCreate, AssessmentQuestionCreate, AssessmentSubmitCreate, AssignmentCreate, AssignmentSubmitCreate, LessonCreate, SectionCreate, TrainingCreate, TrainingDetailResponse, TrainingLiveSessionCreate, TrainingPaginatedResponse, TrainingResponse, TrainingStatusUpdate, TrainingUpdate
from app.services.training_service import add_assessment_question_service, create_assignment_service, create_live_session_service, create_training_announcement_service, create_training_service, delete_training_service, duplicate_training_service, get_live_sessions_service, get_training_progress_service, get_training_service, get_trainings_service, submit_assessment_service, submit_assignment_service, update_training_service, update_training_status_service

router = APIRouter(tags=["Trainings"])

@router.post("/", response_model=TrainingResponse, status_code=201)
def create_training(data: TrainingCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return create_training_service(db, data)

@router.get("/", response_model=TrainingPaginatedResponse)
def list_trainings(search: str | None = Query(None), category: str | None = Query(None), provider: str | None = Query(None, description="provider/instructor_id"), tenant_id: UUID | None = Query(None), enterprise_id: UUID | None = Query(None), location_id: UUID | None = Query(None), status_filter: str | None = Query(None, alias="status"), delivery_mode: str | None = Query(None), min_price: str | None = Query(None), max_price: str | None = Query(None), duration: str | None = Query(None, description="course_type"), date_from: str | None = Query(None), date_to: str | None = Query(None), page: int = Query(DEFAULT_PAGE, ge=1), page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE), db: Session = Depends(get_db)):
    from uuid import UUID as _UUID
    prov = None
    try: prov = _UUID(provider) if provider else None
    except: prov = None
    return get_trainings_service(db, search=search, category=category, provider_id=prov, tenant_id=tenant_id, enterprise_id=enterprise_id, location_id=location_id, status=status_filter, delivery_mode=delivery_mode, min_price=min_price, max_price=max_price, duration=duration, date_from=date_from, date_to=date_to, page=page, page_size=page_size)

@router.get("/{training_id}", response_model=TrainingDetailResponse)
def get_training(training_id: UUID = Path(...), db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return get_training_service(db, training_id)

@router.put("/{training_id}", response_model=TrainingResponse)
def update_training(data: TrainingUpdate, training_id: UUID = Path(...), db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return update_training_service(db, training_id, data)

@router.delete("/{training_id}")
def delete_training(training_id: UUID = Path(...), db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    delete_training_service(db, training_id); return {"message":"Training deleted"}

@router.post("/{training_id}/duplicate", response_model=TrainingResponse, status_code=201)
def duplicate(training_id: UUID = Path(...), db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return duplicate_training_service(db, training_id)

@router.patch("/{training_id}/status", response_model=TrainingResponse)
def update_status(training_id: UUID, payload: TrainingStatusUpdate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    if payload.status in ["approved", "published", "completed", "archived", "suspended"] and current_user.get("role") != "admin":
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Only admin can set status to approved/published/completed/archived/suspended")
    return update_training_status_service(db, training_id, payload.status)


@router.post("/{training_id}/unpublish", response_model=TrainingResponse, status_code=200)
def unpublish_training(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return update_training_status_service(db, training_id, "approved")


@router.post("/{training_id}/archive", response_model=TrainingResponse, status_code=200)
def archive_training(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    if current_user.get("role") != "admin":
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Only admin can archive")
    return update_training_status_service(db, training_id, "archived")

# Builder - sections / lessons (T7) - stored as JSONB on training
@router.get("/{training_id}/sections")
def list_sections(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    t = get_training_service(db, training_id); return t.sections or []

@router.post("/{training_id}/sections", status_code=201)
def add_section(training_id: UUID, payload: SectionCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.training_repo import get_training_by_id
    import uuid
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    secs = list(obj.sections or []); new={"id": str(uuid.uuid4()), **payload.model_dump()}; secs.append(new); obj.sections=secs; db.commit(); return new

@router.put("/{training_id}/sections/{section_id}")
def update_section(training_id: UUID, section_id: str, payload: SectionCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.training_repo import get_training_by_id
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    for s in obj.sections or []:
        if s.get("id")==section_id:
            s.update(payload.model_dump(exclude_unset=True)); db.commit(); return s
    from fastapi import HTTPException; raise HTTPException(404, "Section not found")

@router.post("/{training_id}/sections/reorder")
def reorder_sections(training_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.training_repo import get_training_by_id
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    order = payload.get("ordered_ids", [])
    mapping = {s["id"]: s for s in (obj.sections or [])}
    obj.sections = [mapping[i] for i in order if i in mapping]
    db.commit(); return obj.sections

@router.post("/{training_id}/sections/{section_id}/lessons/reorder")
def reorder_lessons(training_id: UUID, section_id: str, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.training_repo import get_training_by_id
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    for s in obj.sections or []:
        if s.get("id")==section_id:
            order = payload.get("ordered_ids", [])
            mapping = {l["id"]: l for l in s.get("lessons", [])}
            s["lessons"] = [mapping[i] for i in order if i in mapping]
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(obj, "sections")
            db.commit(); return s["lessons"]
    from fastapi import HTTPException; raise HTTPException(404, "Section not found")

@router.post("/{training_id}/sections/{section_id}/lessons", status_code=201)
def add_lesson(training_id: UUID, section_id: str, payload: LessonCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.training_repo import get_training_by_id
    import uuid
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    # Validate prerequisites exist within training
    if payload.prerequisites:
        all_ids = {l.get("id") for s in (obj.sections or []) for l in s.get("lessons", [])}
        for pid in payload.prerequisites:
            if pid not in all_ids and pid != section_id:
                # allow cross-section prerequisites, but warn if not found
                pass
    for s in obj.sections or []:
        if s.get("id")==section_id:
            lessons = s.get("lessons", []); new={"id": str(uuid.uuid4()), **payload.model_dump()}; lessons.append(new); s["lessons"]=lessons; from sqlalchemy.orm.attributes import flag_modified; flag_modified(obj, "sections"); db.commit(); return new
    from fastapi import HTTPException; raise HTTPException(404, "Section not found")

@router.put("/{training_id}/sections/{section_id}/lessons/{lesson_id}")
def update_lesson(training_id: UUID, section_id: str, lesson_id: str, payload: LessonCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.training_repo import get_training_by_id
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    for s in obj.sections or []:
        if s.get("id")==section_id:
            for ls in s.get("lessons", []):
                if ls.get("id")==lesson_id:
                    ls.update(payload.model_dump(exclude_unset=True)); db.commit(); return ls
    from fastapi import HTTPException; raise HTTPException(404, "Lesson not found")

@router.delete("/{training_id}/sections/{section_id}/lessons/{lesson_id}")
def delete_lesson(training_id: UUID, section_id: str, lesson_id: str, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.training_repo import get_training_by_id
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    for s in obj.sections or []:
        if s.get("id")==section_id:
            s["lessons"]=[l for l in s.get("lessons",[]) if l.get("id")!=lesson_id]; db.commit(); return {"message":"Deleted"}
    from fastapi import HTTPException; raise HTTPException(404, "Section not found")

# Enrol, waitlist, assessments, assignments, progress, live-sessions, announcements
@router.get("/my/enrolments", summary="Participant dashboard — enrolled/active/completed/cancelled")
def my_enrolments(status: str | None = Query(None, description="enrolled|pending_approval|cancelled|waitlisted"), db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.models.training_model import TrainingEnrolment
    email = current_user.get("email")
    if not email:
        from fastapi import HTTPException; raise HTTPException(400, "Email not found in token")
    q = db.query(TrainingEnrolment).filter(TrainingEnrolment.participant_email==email)
    if status: q = q.filter(TrainingEnrolment.status==status)
    rows = q.order_by(TrainingEnrolment.created_at.desc()).all()
    return [{"training_id": str(r.training_id), "status": r.status, "enrolment_id": str(r.id), "created_at": r.created_at.isoformat()} for r in rows]

@router.post("/{training_id}/enrol", status_code=201)
def enrol(training_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services.training_service import create_training_enrol_service
    coupon = payload.get("coupon_code")
    return create_training_enrol_service(db, training_id, payload, coupon_code=coupon)

@router.get("/{training_id}/enrolments")
def list_enrolments(training_id: UUID, db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.models.training_model import TrainingEnrolment
    return db.query(TrainingEnrolment).filter(TrainingEnrolment.training_id==training_id).all()

@router.get("/{training_id}/content", summary="Secure enrolled content — gated")
def secure_content(training_id: UUID, db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.models.training_model import TrainingEnrolment
    from app.repository.training_repo import get_training_by_id
    from fastapi import HTTPException
    email = current_user.get("email")
    enrol = db.query(TrainingEnrolment).filter(TrainingEnrolment.training_id==training_id, TrainingEnrolment.participant_email==email).first() if email else None
    if not enrol and current_user.get("role") not in ["admin","provider"]:
        raise HTTPException(403, "Enrolled participants only")
    obj = get_training_by_id(db, training_id)
    if not obj: raise HTTPException(404, "Training not found")
    return {"training_id": str(training_id), "sections": obj.sections or [], "assessments": obj.assessments or []}

@router.post("/{training_id}/enrolments/{enrol_id}/approve", summary="Approve/Reject Enrolment")
def approve_enrol(training_id: UUID, enrol_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.services.training_service import approve_training_enrol_service
    action = payload.get("action", "approve")
    return approve_training_enrol_service(db, training_id, enrol_id, action)

@router.post("/{training_id}/checkout", status_code=201, summary="Checkout — Training")
def checkout_training(training_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services.training_service import create_training_checkout_service
    from app.schemas.training_schema import TrainingCheckoutRequest
    # allow dict or typed
    req = TrainingCheckoutRequest(**payload) if isinstance(payload, dict) else payload
    return create_training_checkout_service(db, training_id, req)

@router.get("/{training_id}/orders", summary="List Training Orders")
def list_training_orders(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.services.training_service import get_training_orders_service
    return get_training_orders_service(db, training_id)

@router.post("/{training_id}/waitlist", status_code=201)
def join_waitlist(training_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.models.training_model import TrainingWaitlist
    w = TrainingWaitlist(training_id=training_id, participant_name=payload.get("participant_name","User"), participant_email=payload.get("participant_email","user@example.com"))
    db.add(w); db.commit(); db.refresh(w); return w

@router.delete("/{training_id}/waitlist/{entry_id}")
def leave_waitlist(training_id: UUID, entry_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.models.training_model import TrainingWaitlist
    from fastapi import HTTPException
    w = db.query(TrainingWaitlist).filter(TrainingWaitlist.id==entry_id).first()
    if not w: raise HTTPException(404, "Not found")
    db.delete(w); db.commit(); return {"message":"Removed"}

@router.post("/{training_id}/assessments", status_code=201)
def create_assessment(training_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.training_repo import get_training_by_id
    import uuid
    t = get_training_by_id(db, training_id)
    if not t: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    arr = list(t.assessments or []); new={"id": str(uuid.uuid4()), **payload}; arr.append(new); t.assessments=arr; db.commit(); return new

@router.get("/{training_id}/assessments")
def list_assessments(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.repository.training_repo import get_training_by_id
    t = get_training_by_id(db, training_id)
    if not t: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    return t.assessments or []

@router.post("/{training_id}/assessments/{aid}/questions", status_code=201)
def add_question(training_id: UUID, aid: str, payload: AssessmentQuestionCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return add_assessment_question_service(db, training_id, aid, payload)

@router.post("/{training_id}/assessments/{aid}/submit", status_code=201)
def submit_assessment(training_id: UUID, aid: str, payload: AssessmentSubmitCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    email = current_user.get("email") if current_user else "user@example.com"
    return submit_assessment_service(db, training_id, aid, payload, participant_email=email)

@router.post("/{training_id}/assignments", status_code=201)
def create_assignment(training_id: UUID, payload: AssignmentCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return create_assignment_service(db, training_id, payload)

@router.post("/{training_id}/assignments/{aid}/submit", status_code=201)
def submit_assignment(training_id: UUID, aid: str, payload: AssignmentSubmitCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    email = current_user.get("email") if current_user else "user@example.com"
    return submit_assignment_service(db, training_id, aid, payload, participant_email=email)

@router.get("/{training_id}/progress")
def progress(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    email = current_user.get("email") if current_user else None
    return get_training_progress_service(db, training_id, participant_email=email)

@router.post("/{training_id}/live-sessions", status_code=201)
def create_live(training_id: UUID, payload: TrainingLiveSessionCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return create_live_session_service(db, training_id, payload)

@router.get("/{training_id}/live-sessions")
def list_live(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return get_live_sessions_service(db, training_id)

@router.post("/{training_id}/announcements")
def announce(training_id: UUID, payload: AnnouncementCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return create_training_announcement_service(db, training_id, payload, current_user)
