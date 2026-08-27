from uuid import UUID
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.models.enterprise_model import Enterprise
from app.models.location_model import EnterpriseLocation
from app.repository.training_repo import create_training, delete_training, get_training_by_id, get_trainings, update_training
from app.repository.query_utils import build_pagination_meta
from app.schemas.training_schema import TrainingDetailResponse, TrainingListItemResponse, TrainingPaginatedResponse, TrainingResponse
from app.services.response_mappers import map_training_detail, map_training_list_item, map_training_write

def _validate(db: Session, eid: UUID, lid: UUID | None):
    ent = db.query(Enterprise).filter(Enterprise.id==eid, Enterprise.is_deleted.is_(False)).first()
    if not ent: raise HTTPException(status_code=404, detail="Enterprise not found")
    if ent.status in ("draft", "pending", "inactive"):
        raise HTTPException(status_code=400, detail=f"Enterprise not approved (status={ent.status}). Trainings can only be created under an approved business/profile.")
    if lid:
        loc = db.query(EnterpriseLocation).filter(EnterpriseLocation.id==lid, EnterpriseLocation.enterprise_id==eid, EnterpriseLocation.is_deleted.is_(False)).first()
        if not loc: raise HTTPException(status_code=404, detail="Location not found for this enterprise")

def create_training_service(db: Session, data):
    _validate(db, data.enterprise_id, data.location_id)
    return TrainingResponse.model_validate(map_training_write(create_training(db, data)))

def get_trainings_service(db: Session, **kw):
    items, total = get_trainings(db, **kw)
    return TrainingPaginatedResponse(items=[TrainingListItemResponse.model_validate(map_training_list_item(i)) for i in items], pagination=build_pagination_meta(total, kw.get("page",1), kw.get("page_size",20)))

def get_training_service(db: Session, tid: UUID):
    obj = get_training_by_id(db, tid)
    if not obj: raise HTTPException(status_code=404, detail="Training not found")
    return TrainingDetailResponse.model_validate(map_training_detail(obj))

def update_training_service(db: Session, tid: UUID, data):
    obj = get_training_by_id(db, tid, include_deleted=True)
    if not obj or obj.is_deleted: raise HTTPException(status_code=404, detail="Training not found")
    lid = data.location_id if getattr(data,"location_id",None) is not None else obj.location_id
    _validate(db, obj.enterprise_id, lid)
    return TrainingResponse.model_validate(map_training_write(update_training(db, obj, data)))

def delete_training_service(db: Session, tid: UUID):
    obj = get_training_by_id(db, tid)
    if not obj: raise HTTPException(status_code=404, detail="Training not found")
    return delete_training(db, obj)

def duplicate_training_service(db: Session, tid: UUID):
    obj = get_training_by_id(db, tid)
    if not obj: raise HTTPException(status_code=404, detail="Training not found")
    payload = {c.key: getattr(obj, c.key) for c in obj.__table__.columns if c.key not in ("id","created_at","updated_at")}
    payload["status"]="draft"; payload["is_deleted"]=False
    from app.models.training_model import Training
    clone=Training(**payload); db.add(clone); db.commit(); db.refresh(clone)
    return TrainingResponse.model_validate(map_training_write(clone))

def update_training_status_service(db: Session, tid: UUID, st: str):
    obj = get_training_by_id(db, tid, include_deleted=True)
    if not obj or obj.is_deleted: raise HTTPException(status_code=404, detail="Training not found")
    VALID = {
        "draft": ["pending_approval", "cancelled"],
        "pending_approval": ["approved", "cancelled"],
        "approved": ["published", "cancelled"],
        "published": ["cancelled", "completed", "suspended", "archived"],
        "suspended": ["published", "cancelled"],
        "completed": [], "cancelled": ["draft"], "archived": ["draft"],
    }
    allowed = VALID.get(obj.status, [])
    if allowed and st not in allowed:
        raise HTTPException(status_code=400, detail=f"Cannot transition from '{obj.status}' to '{st}'. Allowed: {allowed}")
    obj.status=st; db.commit(); db.refresh(obj); return TrainingResponse.model_validate(map_training_write(obj))

# ---- Assessment / Assignment / Progress / LiveSession services (real implementations) ----

def _get_training_or_404(db: Session, tid: UUID):
    obj = get_training_by_id(db, tid)
    if not obj: raise HTTPException(status_code=404, detail="Training not found")
    return obj

def add_assessment_question_service(db: Session, tid: UUID, aid: str, data):
    import uuid as _uuid, copy
    from sqlalchemy.orm.attributes import flag_modified
    t = _get_training_or_404(db, tid)
    assessments = copy.deepcopy(t.assessments or [])
    for a in assessments:
        if str(a.get("id")) == str(aid):
            qs = a.get("questions", [])
            new_q = {"id": str(_uuid.uuid4()), **data.model_dump()}
            qs.append(new_q)
            a["questions"] = qs
            t.assessments = assessments
            flag_modified(t, "assessments")
            db.commit(); db.refresh(t)
            return new_q
    raise HTTPException(status_code=404, detail="Assessment not found")

def submit_assessment_service(db: Session, tid: UUID, aid: str, payload, participant_email: str = "user@example.com"):
    import uuid as _uuid
    from app.models.training_model import TrainingAssessmentSubmission
    t = _get_training_or_404(db, tid)
    assessments = t.assessments or []
    target = next((a for a in assessments if str(a.get("id")) == str(aid)), None)
    if not target:
        raise HTTPException(status_code=404, detail="Assessment not found")
    questions = target.get("questions", [])
    answers = payload.answers if hasattr(payload, "answers") else payload.get("answers", []) if isinstance(payload, dict) else []
    # Build lookup
    qmap = {str(q.get("id")): q for q in questions}
    total = sum(int(q.get("points", 1)) for q in questions) or len(questions)
    score = 0
    for ans in answers:
        qid = str(ans.get("question_id") or ans.get("id") or "")
        given = str(ans.get("answer", "")).strip().lower()
        q = qmap.get(qid)
        if not q:
            continue
        correct = str(q.get("correct_answer", "")).strip().lower()
        if given and correct and given == correct:
            score += int(q.get("points", 1))
    passing = int(target.get("passing_score", total * 0.6 if total else 0))
    passed = score >= passing
    sub = TrainingAssessmentSubmission(training_id=tid, assessment_id=str(aid), participant_email=participant_email, answers=answers, score=str(score), passed=passed)
    db.add(sub); db.commit(); db.refresh(sub)
    return {"score": score, "passed": passed, "total_points": total, "feedback": "Passed" if passed else "Failed", "assessment_id": str(aid), "submission_id": str(sub.id)}

def create_assignment_service(db: Session, tid: UUID, data):
    import uuid as _uuid, copy
    from sqlalchemy.orm.attributes import flag_modified
    t = _get_training_or_404(db, tid)
    assignments = copy.deepcopy(t.assignments or [])
    new = {"id": str(_uuid.uuid4()), **data.model_dump(mode="json")}
    assignments.append(new)
    t.assignments = assignments
    flag_modified(t, "assignments")
    db.commit(); db.refresh(t)
    return new

def submit_assignment_service(db: Session, tid: UUID, aid: str, payload, participant_email: str = "user@example.com"):
    import uuid as _uuid
    from app.models.training_model import TrainingAssignmentSubmission
    t = _get_training_or_404(db, tid)
    assignments = t.assignments or []
    target = next((a for a in assignments if str(a.get("id")) == str(aid)), None)
    if not target:
        raise HTTPException(status_code=404, detail="Assignment not found")
    file_url = payload.file_url if hasattr(payload, "file_url") else payload.get("file_url") if isinstance(payload, dict) else None
    text = payload.submission_text if hasattr(payload, "submission_text") else payload.get("submission_text") if isinstance(payload, dict) else None
    sub = TrainingAssignmentSubmission(training_id=tid, assignment_id=str(aid), participant_email=participant_email, file_url=file_url, submission_text=text)
    db.add(sub); db.commit(); db.refresh(sub)
    return {"id": str(sub.id), "submitted_at": sub.submitted_at.isoformat(), "grade": None, "feedback": None, "assignment_id": str(aid)}

def get_training_progress_service(db: Session, tid: UUID, participant_email: str | None = None):
    t = _get_training_or_404(db, tid)
    sections = t.sections or []
    total_sections = len(sections)
    total_lessons = sum(len(s.get("lessons", [])) for s in sections)
    # try to load progress row
    completed_sections = []
    completed_lessons = []
    certificate_url = None
    if participant_email:
        from app.models.training_model import TrainingProgress
        prog = db.query(TrainingProgress).filter(TrainingProgress.training_id == tid, TrainingProgress.participant_email == participant_email).first()
        if prog:
            completed_sections = prog.sections_completed or []
            completed_lessons = prog.lessons_completed or []
            certificate_url = prog.certificate_url
    # if no progress row, estimate 0
    sections_done = len(completed_sections)
    lessons_done = len(completed_lessons)
    overall = round((lessons_done / total_lessons * 100) if total_lessons else (sections_done / total_sections * 100 if total_sections else 0), 2)
    # Build detail
    sections_detail = [{"section_id": s.get("id"), "section_title": s.get("title"), "lessons_done": sum(1 for l in s.get("lessons", []) if l.get("id") in completed_lessons), "total_lessons": len(s.get("lessons", []))} for s in sections]
    lessons_detail = []
    for s in sections:
        for l in s.get("lessons", []):
            lessons_detail.append({"lesson_id": l.get("id"), "lesson_title": l.get("title"), "is_completed": l.get("id") in completed_lessons})
    return {"overall_percent": overall, "sections_done": sections_done, "total_sections": total_sections, "lessons_done": lessons_done, "total_lessons": total_lessons, "certificate_url": certificate_url, "sections_detail": sections_detail, "lessons_detail": lessons_detail}

def create_live_session_service(db: Session, tid: UUID, data):
    from app.models.training_model import TrainingLiveSession
    _get_training_or_404(db, tid)
    obj = TrainingLiveSession(training_id=tid, title=data.title, description=data.description, scheduled_at=data.scheduled_at, duration_minutes=str(data.duration_minutes), meeting_link=data.meeting_link, meeting_provider=data.meeting_provider)
    db.add(obj); db.commit(); db.refresh(obj)
    return {"id": str(obj.id), "title": obj.title, "scheduled_at": obj.scheduled_at.isoformat(), "duration_minutes": int(obj.duration_minutes) if obj.duration_minutes else None, "meeting_link": obj.meeting_link, "meeting_provider": obj.meeting_provider, "status": obj.status, "recording_url": obj.recording_url}

def get_live_sessions_service(db: Session, tid: UUID):
    from app.models.training_model import TrainingLiveSession
    _get_training_or_404(db, tid)
    rows = db.query(TrainingLiveSession).filter(TrainingLiveSession.training_id == tid).order_by(TrainingLiveSession.scheduled_at).all()
    return [{"id": str(r.id), "title": r.title, "scheduled_at": r.scheduled_at.isoformat(), "duration_minutes": int(r.duration_minutes) if r.duration_minutes else None, "meeting_link": r.meeting_link, "meeting_provider": r.meeting_provider, "status": r.status, "recording_url": r.recording_url} for r in rows]

def create_training_announcement_service(db: Session, tid: UUID, data, current_user: dict | None = None):
    _get_training_or_404(db, tid)
    # For now persist as simple dict return; could add table later
    import uuid as _uuid, datetime
    return {"id": str(_uuid.uuid4()), "training_id": str(tid), "title": data.title, "message": data.message, "sent_at": datetime.datetime.utcnow().isoformat(), "channel": data.channel}
