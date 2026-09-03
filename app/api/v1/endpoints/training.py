from uuid import UUID
from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session
from app.core.dependencies import get_current_user, require_roles
from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.training_schema import AnnouncementCreate, AssessmentQuestionCreate, AssessmentSubmitCreate, AssignmentCreate, AssignmentSubmitCreate, LessonCreate, SectionCreate, TrainingCreate, TrainingDetailResponse, TrainingLiveSessionCreate, TrainingPaginatedResponse, TrainingResponse, TrainingStatusUpdate, TrainingUpdate
from app.services.training_service import add_assessment_question_service, complete_lesson_service, create_assignment_service, create_live_session_service, create_training_announcement_service, create_training_service, delete_training_service, duplicate_training_service, get_certificate_service, get_live_sessions_service, get_training_progress_service, get_training_service, get_trainings_service, grade_assignment_service, record_live_attendance_service, submit_assessment_service, submit_assignment_service, update_training_service, update_training_status_service, publish_training_service, unpublish_training_service, suspend_training_service, cancel_training_service, delete_section_service, get_lesson_service, list_lesson_topics_service, add_lesson_topic_service, update_lesson_topic_service, delete_lesson_topic_service, update_assessment_service, delete_assessment_service, delete_assessment_question_service, filter_assessments, get_secure_training_content_service, reply_discussion_service, get_moderation_history_service, list_training_announcements_service, get_live_attendance_service, export_live_attendance_service, approve_training_enrol_service

router = APIRouter(tags=["Trainings"])

@router.post("/", response_model=TrainingResponse, status_code=201)
def create_training(data: TrainingCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return create_training_service(db, data, current_user)

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

@router.patch("/{training_id}/status", response_model=TrainingResponse, summary="Update training status (generic transition)")
def update_status(training_id: UUID, payload: TrainingStatusUpdate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    if payload.status == "approved" and current_user.get("role") not in ("admin", "super_admin"):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Only admin can approve")
    return update_training_status_service(db, training_id, payload.status)


@router.post("/{training_id}/publish", response_model=TrainingResponse, status_code=200, summary="Publish training")
def publish_training(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return publish_training_service(db, training_id, current_user)


@router.post("/{training_id}/unpublish", response_model=TrainingResponse, status_code=200, summary="Unpublish training (sets status=unpublished)")
def unpublish_training(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return unpublish_training_service(db, training_id, current_user)


@router.post("/{training_id}/suspend", response_model=TrainingResponse, status_code=200, summary="Suspend published training")
def suspend_training(training_id: UUID, payload: TrainingStatusUpdate | None = None, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    reason = payload.reason if payload else None
    return suspend_training_service(db, training_id, reason=reason, current_user=current_user)


@router.post("/{training_id}/cancel", response_model=TrainingResponse, status_code=200, summary="Cancel training")
def cancel_training(training_id: UUID, payload: TrainingStatusUpdate | None = None, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    reason = payload.reason if payload else None
    return cancel_training_service(db, training_id, reason=reason, current_user=current_user)


@router.post("/{training_id}/archive", response_model=TrainingResponse, status_code=200, summary="Archive training")
def archive_training(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return update_training_status_service(db, training_id, "archived")


@router.get("/{training_id}/moderation-history", summary="Admin moderation / rejection history")
def moderation_history(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return get_moderation_history_service(db, training_id)

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
    from sqlalchemy.orm.attributes import flag_modified
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    for s in obj.sections or []:
        if s.get("id")==section_id:
            s.update(payload.model_dump(exclude_unset=True)); flag_modified(obj, "sections"); db.commit(); return s
    from fastapi import HTTPException; raise HTTPException(404, "Section not found")

@router.delete("/{training_id}/sections/{section_id}", summary="Delete section/module")
def delete_section(training_id: UUID, section_id: str, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return delete_section_service(db, training_id, section_id)

@router.get("/{training_id}/sections/{section_id}/lessons/{lesson_id}", summary="Get single lesson")
def get_lesson(training_id: UUID, section_id: str, lesson_id: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return get_lesson_service(db, training_id, section_id, lesson_id, current_user)

@router.get("/{training_id}/sections/{section_id}/lessons/{lesson_id}/topics", summary="List lesson topics")
def list_topics(training_id: UUID, section_id: str, lesson_id: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return list_lesson_topics_service(db, training_id, section_id, lesson_id)

@router.post("/{training_id}/sections/{section_id}/lessons/{lesson_id}/topics", status_code=201, summary="Add lesson topic")
def add_topic(training_id: UUID, section_id: str, lesson_id: str, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return add_lesson_topic_service(db, training_id, section_id, lesson_id, payload)

@router.put("/{training_id}/sections/{section_id}/lessons/{lesson_id}/topics/{topic_id}", summary="Update lesson topic")
def update_topic(training_id: UUID, section_id: str, lesson_id: str, topic_id: str, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return update_lesson_topic_service(db, training_id, section_id, lesson_id, topic_id, payload)

@router.delete("/{training_id}/sections/{section_id}/lessons/{lesson_id}/topics/{topic_id}", summary="Delete lesson topic")
def delete_topic(training_id: UUID, section_id: str, lesson_id: str, topic_id: str, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return delete_lesson_topic_service(db, training_id, section_id, lesson_id, topic_id)

@router.post("/{training_id}/sections/reorder", summary="Reorder sections/modules")
def reorder_sections(training_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.training_repo import get_training_by_id
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    order = payload.get("ordered_ids", [])
    mapping = {s["id"]: s for s in (obj.sections or []) if s.get("id")}
    ordered = [mapping[i] for i in order if i in mapping]
    # Preserve sections not in ordered_ids (avoid data loss)
    remaining = [s for s in (obj.sections or []) if s.get("id") not in order]
    obj.sections = ordered + remaining
    db.commit(); return obj.sections

@router.post("/{training_id}/modules/reorder", summary="Reorder modules (alias)")
def reorder_modules(training_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.training_repo import get_training_by_id
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    order = payload.get("ordered_ids", [])
    mapping = {s["id"]: s for s in (obj.sections or []) if s.get("id")}
    ordered = [mapping[i] for i in order if i in mapping]
    remaining = [s for s in (obj.sections or []) if s.get("id") not in order]
    obj.sections = ordered + remaining
    db.commit(); return obj.sections

@router.post("/{training_id}/sections/{section_id}/lessons/reorder")
def reorder_lessons(training_id: UUID, section_id: str, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.training_repo import get_training_by_id
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    for s in obj.sections or []:
        if s.get("id")==section_id:
            order = payload.get("ordered_ids", [])
            mapping = {l["id"]: l for l in s.get("lessons", []) if l.get("id")}
            ordered = [mapping[i] for i in order if i in mapping]
            remaining = [l for l in s.get("lessons", []) if l.get("id") not in order]
            s["lessons"] = ordered + remaining
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
                from fastapi import HTTPException; raise HTTPException(status_code=400, detail=f"Prerequisite lesson {pid} not found")
    for s in obj.sections or []:
        if s.get("id")==section_id:
            lessons = s.get("lessons", []); new={"id": str(uuid.uuid4()), **payload.model_dump()}; lessons.append(new); s["lessons"]=lessons; from sqlalchemy.orm.attributes import flag_modified; flag_modified(obj, "sections"); db.commit(); return new
    from fastapi import HTTPException; raise HTTPException(404, "Section not found")

@router.put("/{training_id}/sections/{section_id}/lessons/{lesson_id}")
def update_lesson(training_id: UUID, section_id: str, lesson_id: str, payload: LessonCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.training_repo import get_training_by_id
    from sqlalchemy.orm.attributes import flag_modified
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    for s in obj.sections or []:
        if s.get("id")==section_id:
            for ls in s.get("lessons", []):
                if ls.get("id")==lesson_id:
                    ls.update(payload.model_dump(exclude_unset=True)); flag_modified(obj, "sections"); db.commit(); return ls
    from fastapi import HTTPException; raise HTTPException(404, "Lesson not found")

@router.delete("/{training_id}/sections/{section_id}/lessons/{lesson_id}")
def delete_lesson(training_id: UUID, section_id: str, lesson_id: str, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.training_repo import get_training_by_id
    from sqlalchemy.orm.attributes import flag_modified
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    for s in obj.sections or []:
        if s.get("id")==section_id:
            s["lessons"]=[l for l in s.get("lessons",[]) if l.get("id")!=lesson_id]; flag_modified(obj, "sections"); db.commit(); return {"message":"Deleted"}
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

@router.get("/{training_id}/content", summary="Secure enrolled content — draft/preview/release gated")
def secure_content(training_id: UUID, db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    return get_secure_training_content_service(db, training_id, current_user)

@router.delete("/{training_id}/enrolments/{enrol_id}", summary="Cancel enrolment — access-expiry & waitlist")
def cancel_enrol(training_id: UUID, enrol_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services.training_service import cancel_training_enrol_service
    email=current_user.get("email")
    # allow provider/admin to cancel any, participant only own
    if current_user.get("role") in ["admin","provider"]:
        return cancel_training_enrol_service(db, training_id, enrol_id)
    return cancel_training_enrol_service(db, training_id, enrol_id, participant_email=email)

@router.post("/{training_id}/enrolments/{enrol_id}/approve", summary="Approve/Reject enrolment with optional reason")
def approve_enrol(training_id: UUID, enrol_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    action = payload.get("action", "approve")
    reason = payload.get("reason")
    return approve_training_enrol_service(db, training_id, enrol_id, action, reason=reason, current_user=current_user)

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

@router.post("/{training_id}/assessments", status_code=201, summary="Create quizzes/tests/assessments/surveys — supports pre-course/module/final/feedback level, pass/attempt/time, publication, randomise")
def create_assessment(training_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.training_repo import get_training_by_id
    import uuid
    t = get_training_by_id(db, training_id)
    if not t: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    arr = list(t.assessments or []); new={"id": str(uuid.uuid4()), **payload}; arr.append(new); t.assessments=arr; db.commit(); return new

@router.get("/{training_id}/assessments", summary="List assessments — filter by module_id / lesson_id")
def list_assessments(training_id: UUID, module_id: str | None = Query(None, description="Filter by section/module id"), lesson_id: str | None = Query(None, description="Filter by lesson id"), randomize: bool = Query(False, description="Randomise questions/answers"), db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.repository.training_repo import get_training_by_id
    import random as _rnd, copy
    t = get_training_by_id(db, training_id)
    if not t: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    out = filter_assessments(copy.deepcopy(t.assessments or []), module_id, lesson_id)
    role = current_user.get("role") if current_user else None
    if role not in ["admin", "provider"]:
        for a in out:
            for q in a.get("questions",[]):
                q.pop("correct_answer", None)
                q.pop("explanation", None)
    if randomize:
        for a in out:
            qs=a.get("questions",[])
            _rnd.shuffle(qs)
            for q in qs:
                if q.get("options"): _rnd.shuffle(q["options"])
            a["questions"]=qs
    return out

@router.put("/{training_id}/assessments/{aid}", summary="Update assessment metadata")
def update_assessment(training_id: UUID, aid: str, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return update_assessment_service(db, training_id, aid, payload)

@router.delete("/{training_id}/assessments/{aid}", summary="Delete assessment")
def delete_assessment(training_id: UUID, aid: str, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return delete_assessment_service(db, training_id, aid)

@router.get("/{training_id}/question-bank", summary="Question bank — reusable questions")
def question_bank(training_id: UUID, db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.services.training_service import get_question_bank_service
    return get_question_bank_service(db, training_id)

@router.post("/{training_id}/assessments/{aid}/questions", status_code=201)
def add_question(training_id: UUID, aid: str, payload: AssessmentQuestionCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return add_assessment_question_service(db, training_id, aid, payload)

@router.delete("/{training_id}/assessments/{aid}/questions/{qid}", summary="Delete assessment question")
def delete_question(training_id: UUID, aid: str, qid: str, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return delete_assessment_question_service(db, training_id, aid, qid)

@router.post("/{training_id}/assessments/{aid}/submit", status_code=201, summary="Submit — automatic scoring, pass/attempt/time enforced")
def submit_assessment(training_id: UUID, aid: str, payload: AssessmentSubmitCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    email = current_user.get("email") if current_user else "user@example.com"
    return submit_assessment_service(db, training_id, aid, payload, participant_email=email)

@router.post("/{training_id}/assessments/{aid}/submissions/{sid}/grade", summary="Manual evaluation for written answers")
def grade_assessment(training_id: UUID, aid: str, sid: UUID, payload: dict, db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.services.training_service import grade_assessment_manual_service
    return grade_assessment_manual_service(db, training_id, aid, str(sid), int(payload.get("score") or payload.get("grade") or 0), payload.get("feedback"))

@router.get("/{training_id}/assessments/{aid}/submissions/{sid}/review", summary="Answer explanations & result review")
def review_assessment(training_id: UUID, aid: str, sid: UUID, db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services.training_service import get_assessment_result_service
    return get_assessment_result_service(db, training_id, aid, str(sid), current_user)

@router.post("/{training_id}/assignments", status_code=201)
def create_assignment(training_id: UUID, payload: AssignmentCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return create_assignment_service(db, training_id, payload)

@router.post("/{training_id}/assignments/{aid}/submit", status_code=201, summary="Submit text/links/images/videos/documents — resubmission allowed")
def submit_assignment(training_id: UUID, aid: str, payload: AssignmentSubmitCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    email = current_user.get("email") if current_user else "user@example.com"
    return submit_assignment_service(db, training_id, aid, payload, participant_email=email)

@router.post("/{training_id}/assignments/{aid}/submissions/{sid}/grade", summary="Instructor feedback & grading")
def grade_assignment(training_id: UUID, aid: str, sid: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.services.training_service import grade_assignment_service
    return grade_assignment_service(db, training_id, aid, str(sid), payload.get("grade") or "0", payload.get("feedback"))

@router.post("/{training_id}/progress/complete-lesson", summary="Track lesson/module progress & resume")
def complete_lesson(training_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services.training_service import complete_lesson_service
    email = payload.get("participant_email") or (current_user.get("email") if current_user else None)
    if not email: from fastapi import HTTPException; raise HTTPException(400, "participant_email required")
    return complete_lesson_service(db, training_id, payload.get("lesson_id") or payload.get("id"), email)

@router.get("/{training_id}/live-sessions/{session_id}/attendance", summary="List live session attendance")
def get_live_attendance(training_id: UUID, session_id: str, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return get_live_attendance_service(db, training_id, session_id)

@router.get("/{training_id}/live-sessions/{session_id}/attendance/export", summary="Export live session attendance CSV")
def export_live_attendance(training_id: UUID, session_id: str, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from fastapi.responses import StreamingResponse
    csv_content, sid = export_live_attendance_service(db, training_id, session_id)
    return StreamingResponse(iter([csv_content]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=training_{training_id}_session_{sid}_attendance.csv"})

@router.post("/{training_id}/live-sessions/{session_id}/attendance", summary="Record live session attendance")
def live_attendance(training_id: UUID, session_id: str, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services.training_service import record_live_attendance_service
    email = payload.get("participant_email") or (current_user.get("email") if current_user else None)
    if not email: from fastapi import HTTPException; raise HTTPException(400, "participant_email required")
    return record_live_attendance_service(db, training_id, session_id, email)

@router.get("/{training_id}/certificate", summary="Digital completion certificate")
def get_certificate(training_id: UUID, participant_email: str = Query(...), db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    # Only owner or admin/provider can fetch certificate
    if current_user and current_user.get("role") not in ("admin", "provider") and current_user.get("email") != participant_email:
        from fastapi import HTTPException; raise HTTPException(status_code=403, detail="Not authorized to view this certificate")
    from app.services.training_service import get_certificate_service
    return get_certificate_service(db, training_id, participant_email)

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

@router.get("/{training_id}/calendar.ics", summary="Calendar integration — add-to-calendar ICS")
def calendar_ics(training_id: UUID, db: Session=Depends(get_db)):
    from fastapi.responses import PlainTextResponse
    from app.repository.training_repo import get_training_by_id
    from app.services.calendar_service import event_to_ics
    obj=get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404,"Training not found")
    # map training to event-like for ICS generation
    class _E: pass
    e=_E(); e.id=obj.id; e.title=obj.title; e.description=obj.description or ""; e.start_date=obj.start_date; e.end_date=obj.end_date; e.venue=None; e.meeting_link=None; e.sessions=[]
    # live sessions as sessions
    from app.models.training_model import TrainingLiveSession
    lives=db.query(TrainingLiveSession).filter(TrainingLiveSession.training_id==training_id).all()
    sess=[]
    for ls in lives:
        sess.append({"id": str(ls.id), "title": ls.title, "speaker": "", "session_date": ls.scheduled_at.date().isoformat() if ls.scheduled_at else "", "start_time": ls.scheduled_at.strftime("%H:%M") if ls.scheduled_at else "", "end_time": "", "location": obj.delivery_mode or "", "meeting_link": ls.meeting_link or ""})
    ics=event_to_ics(e, sess)
    return PlainTextResponse(content=ics, media_type="text/calendar", headers={"Content-Disposition": f"attachment; filename=training_{training_id}.ics"})

@router.get("/{training_id}/meeting-link", summary="Secure meeting link — enrolled only (auto/manual)")
def meeting_link(training_id: UUID, db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.models.training_model import TrainingEnrolment, TrainingLiveSession
    from app.repository.training_repo import get_training_by_id
    from fastapi import HTTPException
    email=current_user.get("email")
    enrol=db.query(TrainingEnrolment).filter(TrainingEnrolment.training_id==training_id, TrainingEnrolment.participant_email==email).first() if email else None
    if not enrol and current_user.get("role") not in ["admin","provider"]:
        raise HTTPException(403,"Enrolled participants only")
    lives=db.query(TrainingLiveSession).filter(TrainingLiveSession.training_id==training_id).all()
    return {"training_id": str(training_id), "meeting_links": [{"session_id": str(ls.id), "title": ls.title, "meeting_link": ls.meeting_link, "provider": ls.meeting_provider} for ls in lives]}

# Discussion / Q&A
@router.get("/{training_id}/discussions", summary="Discussion — Q&A list")
def list_discussions(training_id: UUID, db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.repository.training_repo import get_training_by_id
    obj=get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404,"Training not found")
    return getattr(obj, "discussions", []) or []

@router.post("/{training_id}/discussions", status_code=201, summary="Post Q&A")
def post_discussion(training_id: UUID, payload: dict, db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.repository.training_repo import get_training_by_id
    from sqlalchemy.orm.attributes import flag_modified
    import uuid as _uuid
    obj=get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404,"Training not found")
    disc=list(getattr(obj, "discussions", []) or [])
    entry={"id": str(_uuid.uuid4()), "author": current_user.get("email","anonymous"), "question": payload.get("question") or payload.get("text") or "", "answer": None, "created_at": __import__("datetime").datetime.utcnow().isoformat()}
    if not entry["question"].strip():
        from fastapi import HTTPException; raise HTTPException(400, "Question is required")
    disc.append(entry)
    obj.discussions=disc
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(obj, "discussions")
    db.commit()
    return entry

@router.post("/{training_id}/discussions/{discussion_id}/replies", status_code=201, summary="Reply to Q&A / mark answer")
def reply_discussion(training_id: UUID, discussion_id: str, payload: dict, db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    return reply_discussion_service(db, training_id, discussion_id, payload, current_user)

@router.post("/{training_id}/announcements", summary="Create persisted announcement")
def announce(training_id: UUID, payload: AnnouncementCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return create_training_announcement_service(db, training_id, payload, current_user)

@router.get("/{training_id}/announcements", summary="List training announcements")
def list_announcements(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return list_training_announcements_service(db, training_id)


# ---- Training Order Status & Refund ----

from app.schemas.training_schema import TrainingOrderStatusUpdate, TrainingRefundRequest, TrainingRefundApproveRequest
from app.services.training_service import update_training_order_status_service, request_training_refund_service, approve_training_refund_service


@router.patch(
    "/{training_id}/orders/{order_id}/status",
    summary="Update Training Order Status (Admin/Provider)",
)
def update_training_order_status(
    training_id: UUID,
    order_id: UUID,
    payload: TrainingOrderStatusUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_roles(["admin", "provider"])),
):
    return update_training_order_status_service(db, training_id, order_id, payload)


@router.post(
    "/{training_id}/orders/{order_id}/refund",
    summary="Request Training Refund",
)
def request_training_refund(
    training_id: UUID,
    order_id: UUID,
    payload: TrainingRefundRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return request_training_refund_service(db, training_id, order_id, payload)


@router.post(
    "/{training_id}/orders/{order_id}/refund/approve",
    summary="Approve or Reject Training Refund (Admin/Provider)",
)
def approve_training_refund(
    training_id: UUID,
    order_id: UUID,
    payload: TrainingRefundApproveRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_roles(["admin", "provider"])),
):
    return approve_training_refund_service(db, training_id, order_id, payload)
