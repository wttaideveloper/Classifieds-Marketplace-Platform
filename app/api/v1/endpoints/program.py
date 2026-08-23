from uuid import UUID
from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.program_schema import ActivityCreate, PhaseCreate, ProgramCreate, ProgramDetailResponse, ProgramPaginatedResponse, ProgramResponse, ProgramStatusUpdate, ProgramUpdate
from app.services.program_service import create_program_service, delete_program_service, duplicate_program_service, get_program_service, get_programs_service, update_program_service, update_program_status_service
router=APIRouter(tags=["Programs"])
@router.post("/", response_model=ProgramResponse, status_code=201)
def create_program(data: ProgramCreate, db: Session=Depends(get_db)): return create_program_service(db, data)
@router.get("/", response_model=ProgramPaginatedResponse)
def list_programs(search: str|None=Query(None), category: str|None=Query(None), tenant_id: UUID|None=Query(None), enterprise_id: UUID|None=Query(None), location_id: UUID|None=Query(None), status_filter: str|None=Query(None, alias="status"), page:int=Query(DEFAULT_PAGE,ge=1), page_size:int=Query(DEFAULT_PAGE_SIZE,ge=1,le=MAX_PAGE_SIZE), db:Session=Depends(get_db)):
    return get_programs_service(db, search=search, category=category, tenant_id=tenant_id, enterprise_id=enterprise_id, location_id=location_id, status=status_filter, page=page, page_size=page_size)
@router.get("/{program_id}", response_model=ProgramDetailResponse)
def get_program(program_id: UUID=Path(...), db: Session=Depends(get_db)): return get_program_service(db, program_id)
@router.put("/{program_id}", response_model=ProgramResponse)
def update_program(data: ProgramUpdate, program_id: UUID=Path(...), db: Session=Depends(get_db)): return update_program_service(db, program_id, data)
@router.delete("/{program_id}")
def delete_program(program_id: UUID=Path(...), db: Session=Depends(get_db)): delete_program_service(db, program_id); return {"message":"Program deleted"}
@router.post("/{program_id}/duplicate", response_model=ProgramResponse, status_code=201)
def duplicate(program_id: UUID=Path(...), db: Session=Depends(get_db)): return duplicate_program_service(db, program_id)
@router.patch("/{program_id}/status", response_model=ProgramResponse)
def update_status(program_id: UUID, payload: ProgramStatusUpdate, db: Session=Depends(get_db)): return update_program_status_service(db, program_id, payload.status)
# Phases / Activities (P6)
@router.get("/{program_id}/phases")
def list_phases(program_id: UUID, db: Session=Depends(get_db)):
    p=get_program_service(db, program_id); return p.phases or []
@router.post("/{program_id}/phases", status_code=201)
def add_phase(program_id: UUID, payload: PhaseCreate, db: Session=Depends(get_db)):
    from app.repository.program_repo import get_program_by_id; import uuid
    obj=get_program_by_id(db, program_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404,"Program not found")
    phases=list(obj.phases or []); new={"id":str(uuid.uuid4()), **payload.model_dump()}; phases.append(new); obj.phases=phases; db.commit(); return new
@router.put("/{program_id}/phases/{phase_id}")
def update_phase(program_id: UUID, phase_id: str, payload: PhaseCreate, db: Session=Depends(get_db)):
    from app.repository.program_repo import get_program_by_id
    obj=get_program_by_id(db, program_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404,"Program not found")
    for ph in obj.phases or []:
        if ph.get("id")==phase_id: ph.update(payload.model_dump(exclude_unset=True)); db.commit(); return ph
    from fastapi import HTTPException; raise HTTPException(404,"Phase not found")
@router.post("/{program_id}/phases/{phase_id}/activities", status_code=201)
def add_activity(program_id: UUID, phase_id: str, payload: ActivityCreate, db: Session=Depends(get_db)):
    from app.repository.program_repo import get_program_by_id; import uuid
    obj=get_program_by_id(db, program_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404,"Program not found")
    for ph in obj.phases or []:
        if ph.get("id")==phase_id:
            acts=ph.get("activities",[]); new={"id":str(uuid.uuid4()), **payload.model_dump()}; acts.append(new); ph["activities"]=acts; obj.phases=list(obj.phases); db.commit(); return new
    from fastapi import HTTPException; raise HTTPException(404,"Phase not found")
@router.put("/{program_id}/phases/{phase_id}/activities/{activity_id}")
def update_activity(program_id: UUID, phase_id: str, activity_id: str, payload: ActivityCreate, db: Session=Depends(get_db)):
    from app.repository.program_repo import get_program_by_id
    obj=get_program_by_id(db, program_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404,"Program not found")
    for ph in obj.phases or []:
        if ph.get("id")==phase_id:
            for ac in ph.get("activities",[]):
                if ac.get("id")==activity_id: ac.update(payload.model_dump(exclude_unset=True)); db.commit(); return ac
    from fastapi import HTTPException; raise HTTPException(404,"Activity not found")
# Enrol, check-ins, progress, dashboards, surveys, reports (P7-P11)
@router.post("/{program_id}/enrol", status_code=201)
def enrol(program_id: UUID, payload: dict, db: Session=Depends(get_db)):
    from app.models.program_model import ProgramEnrolment
    e=ProgramEnrolment(program_id=program_id, participant_name=payload.get("participant_name","User"), participant_email=payload.get("participant_email","user@example.com"))
    db.add(e); db.commit(); db.refresh(e); return e
@router.post("/{program_id}/check-ins", status_code=201)
def checkin(program_id: UUID, payload: dict, db: Session=Depends(get_db)):
    from app.models.program_model import ProgramCheckin
    c=ProgramCheckin(program_id=program_id, participant_email=payload.get("participant_email","user@example.com"), phase_id=payload.get("phase_id"), notes=payload.get("notes"))
    db.add(c); db.commit(); db.refresh(c); return c
@router.get("/{program_id}/check-ins")
def list_checkins(program_id: UUID, db: Session=Depends(get_db)):
    from app.models.program_model import ProgramCheckin
    return db.query(ProgramCheckin).filter(ProgramCheckin.program_id==program_id).all()
@router.get("/{program_id}/progress")
def progress(program_id: UUID, db: Session=Depends(get_db)): return {"overall":0, "stage":0, "milestone":0}
@router.get("/{program_id}/dashboards/participant")
def dash_participant(program_id: UUID, db: Session=Depends(get_db)): return {"program_id":str(program_id), "role":"participant", "progress":0}
@router.get("/{program_id}/dashboards/provider")
def dash_provider(program_id: UUID, db: Session=Depends(get_db)): return {"program_id":str(program_id), "role":"provider", "progress":0}
@router.post("/{program_id}/surveys", status_code=201)
def create_survey(program_id: UUID, payload: dict, db: Session=Depends(get_db)): import uuid; return {"id":str(uuid.uuid4()), **payload}
@router.post("/{program_id}/reviews", status_code=201)
def create_review(program_id: UUID, payload: dict, db: Session=Depends(get_db)): import uuid; return {"id":str(uuid.uuid4()), **payload}
@router.get("/{program_id}/reports")
def reports(program_id: UUID, type: str=Query("enrolment"), db: Session=Depends(get_db)): return {"program_id":str(program_id), "type":type, "data":{}}
@router.get("/reports/summary")
def summary(enterprise_id: UUID|None=None, db: Session=Depends(get_db)):
    from app.models.program_model import Program
    q=db.query(Program).filter(Program.is_deleted.is_(False))
    if enterprise_id: q=q.filter(Program.enterprise_id==enterprise_id)
    return {"total_programs": q.count(), "by_status":{}}
