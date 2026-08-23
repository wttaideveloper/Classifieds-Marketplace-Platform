from uuid import UUID
from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.training_schema import LessonCreate, SectionCreate, TrainingCreate, TrainingDetailResponse, TrainingPaginatedResponse, TrainingResponse, TrainingStatusUpdate, TrainingUpdate
from app.services.training_service import create_training_service, delete_training_service, duplicate_training_service, get_training_service, get_trainings_service, update_training_service, update_training_status_service

router = APIRouter(tags=["Trainings"])

@router.post("/", response_model=TrainingResponse, status_code=201)
def create_training(data: TrainingCreate, db: Session = Depends(get_db)):
    return create_training_service(db, data)

@router.get("/", response_model=TrainingPaginatedResponse)
def list_trainings(search: str | None = Query(None), category: str | None = Query(None), tenant_id: UUID | None = Query(None), enterprise_id: UUID | None = Query(None), location_id: UUID | None = Query(None), status_filter: str | None = Query(None, alias="status"), page: int = Query(DEFAULT_PAGE, ge=1), page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE), db: Session = Depends(get_db)):
    return get_trainings_service(db, search=search, category=category, tenant_id=tenant_id, enterprise_id=enterprise_id, location_id=location_id, status=status_filter, page=page, page_size=page_size)

@router.get("/{training_id}", response_model=TrainingDetailResponse)
def get_training(training_id: UUID = Path(...), db: Session = Depends(get_db)):
    return get_training_service(db, training_id)

@router.put("/{training_id}", response_model=TrainingResponse)
def update_training(data: TrainingUpdate, training_id: UUID = Path(...), db: Session = Depends(get_db)):
    return update_training_service(db, training_id, data)

@router.delete("/{training_id}")
def delete_training(training_id: UUID = Path(...), db: Session = Depends(get_db)):
    delete_training_service(db, training_id); return {"message":"Training deleted"}

@router.post("/{training_id}/duplicate", response_model=TrainingResponse, status_code=201)
def duplicate(training_id: UUID = Path(...), db: Session = Depends(get_db)):
    return duplicate_training_service(db, training_id)

@router.patch("/{training_id}/status", response_model=TrainingResponse)
def update_status(training_id: UUID, payload: TrainingStatusUpdate, db: Session = Depends(get_db)):
    return update_training_status_service(db, training_id, payload.status)

# Builder - sections / lessons (T7) - stored as JSONB on training
@router.get("/{training_id}/sections")
def list_sections(training_id: UUID, db: Session = Depends(get_db)):
    t = get_training_service(db, training_id); return t.sections or []

@router.post("/{training_id}/sections", status_code=201)
def add_section(training_id: UUID, payload: SectionCreate, db: Session = Depends(get_db)):
    from app.repository.training_repo import get_training_by_id
    import uuid
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    secs = list(obj.sections or []); new={"id": str(uuid.uuid4()), **payload.model_dump()}; secs.append(new); obj.sections=secs; db.commit(); return new

@router.put("/{training_id}/sections/{section_id}")
def update_section(training_id: UUID, section_id: str, payload: SectionCreate, db: Session = Depends(get_db)):
    from app.repository.training_repo import get_training_by_id
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    for s in obj.sections or []:
        if s.get("id")==section_id:
            s.update(payload.model_dump(exclude_unset=True)); db.commit(); return s
    from fastapi import HTTPException; raise HTTPException(404, "Section not found")

@router.post("/{training_id}/sections/reorder")
def reorder_sections(training_id: UUID, payload: dict, db: Session = Depends(get_db)):
    from app.repository.training_repo import get_training_by_id
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    order = payload.get("ordered_ids", [])
    mapping = {s["id"]: s for s in (obj.sections or [])}
    obj.sections = [mapping[i] for i in order if i in mapping]
    db.commit(); return obj.sections

@router.post("/{training_id}/sections/{section_id}/lessons", status_code=201)
def add_lesson(training_id: UUID, section_id: str, payload: LessonCreate, db: Session = Depends(get_db)):
    from app.repository.training_repo import get_training_by_id
    import uuid
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    for s in obj.sections or []:
        if s.get("id")==section_id:
            lessons = s.get("lessons", []); new={"id": str(uuid.uuid4()), **payload.model_dump()}; lessons.append(new); s["lessons"]=lessons; obj.sections=list(obj.sections); db.commit(); return new
    from fastapi import HTTPException; raise HTTPException(404, "Section not found")

@router.put("/{training_id}/sections/{section_id}/lessons/{lesson_id}")
def update_lesson(training_id: UUID, section_id: str, lesson_id: str, payload: LessonCreate, db: Session = Depends(get_db)):
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
def delete_lesson(training_id: UUID, section_id: str, lesson_id: str, db: Session = Depends(get_db)):
    from app.repository.training_repo import get_training_by_id
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    for s in obj.sections or []:
        if s.get("id")==section_id:
            s["lessons"]=[l for l in s.get("lessons",[]) if l.get("id")!=lesson_id]; db.commit(); return {"message":"Deleted"}
    from fastapi import HTTPException; raise HTTPException(404, "Section not found")

# Enrol, waitlist, assessments, assignments, progress, live-sessions, announcements - stubs that keep contract
@router.post("/{training_id}/enrol", status_code=201)
def enrol(training_id: UUID, payload: dict, db: Session = Depends(get_db)):
    from app.models.training_model import TrainingEnrolment
    import uuid
    # minimal enrol
    e = TrainingEnrolment(training_id=training_id, participant_name=payload.get("participant_name","User"), participant_email=payload.get("participant_email","user@example.com"))
    from app.db.database import get_db as _gd
    # use db session
    db.add(e); db.commit(); db.refresh(e); return e

@router.get("/{training_id}/enrolments")
def list_enrolments(training_id: UUID, db: Session = Depends(get_db)):
    from app.models.training_model import TrainingEnrolment
    return db.query(TrainingEnrolment).filter(TrainingEnrolment.training_id==training_id).all()

@router.post("/{training_id}/waitlist", status_code=201)
def join_waitlist(training_id: UUID, payload: dict, db: Session = Depends(get_db)):
    from app.models.training_model import TrainingWaitlist
    w = TrainingWaitlist(training_id=training_id, participant_name=payload.get("participant_name","User"), participant_email=payload.get("participant_email","user@example.com"))
    db.add(w); db.commit(); db.refresh(w); return w

@router.delete("/{training_id}/waitlist/{entry_id}")
def leave_waitlist(training_id: UUID, entry_id: UUID, db: Session = Depends(get_db)):
    from app.models.training_model import TrainingWaitlist
    from fastapi import HTTPException
    w = db.query(TrainingWaitlist).filter(TrainingWaitlist.id==entry_id).first()
    if not w: raise HTTPException(404, "Not found")
    db.delete(w); db.commit(); return {"message":"Removed"}

@router.post("/{training_id}/assessments", status_code=201)
def create_assessment(training_id: UUID, payload: dict, db: Session = Depends(get_db)):
    from app.repository.training_repo import get_training_by_id
    import uuid
    t = get_training_by_id(db, training_id)
    if not t: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    arr = list(t.assessments or []); new={"id": str(uuid.uuid4()), **payload}; arr.append(new); t.assessments=arr; db.commit(); return new

@router.get("/{training_id}/assessments")
def list_assessments(training_id: UUID, db: Session = Depends(get_db)):
    from app.repository.training_repo import get_training_by_id
    t = get_training_by_id(db, training_id)
    if not t: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    return t.assessments or []

@router.post("/{training_id}/assessments/{aid}/questions", status_code=201)
def add_question(training_id: UUID, aid: str, payload: dict, db: Session = Depends(get_db)):
    return {"message":"Question added", "assessment_id": aid, **payload}

@router.post("/{training_id}/assessments/{aid}/submit", status_code=201)
def submit_assessment(training_id: UUID, aid: str, payload: dict, db: Session = Depends(get_db)):
    return {"score": 80, "passed": True, "assessment_id": aid}

@router.post("/{training_id}/assignments", status_code=201)
def create_assignment(training_id: UUID, payload: dict, db: Session = Depends(get_db)):
    return {"id": str(__import__("uuid").uuid4()), **payload}

@router.post("/{training_id}/assignments/{aid}/submit", status_code=201)
def submit_assignment(training_id: UUID, aid: str, payload: dict, db: Session = Depends(get_db)):
    return {"message":"Submitted", "assignment_id": aid}

@router.get("/{training_id}/progress")
def progress(training_id: UUID, db: Session = Depends(get_db)):
    return {"overall_percent": 0, "sections_done": 0, "certificate_url": None}

@router.post("/{training_id}/live-sessions", status_code=201)
def create_live(training_id: UUID, payload: dict, db: Session = Depends(get_db)):
    import uuid; return {"id": str(uuid.uuid4()), "meeting_link": "https://zoom.us/j/xxx", **payload}

@router.get("/{training_id}/live-sessions")
def list_live(training_id: UUID, db: Session = Depends(get_db)):
    return []

@router.post("/{training_id}/announcements")
def announce(training_id: UUID, payload: dict, db: Session = Depends(get_db)):
    return {"message":"Announcement queued"}
