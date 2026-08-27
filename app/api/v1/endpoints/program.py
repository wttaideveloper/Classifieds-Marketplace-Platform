from uuid import UUID
from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session
from app.core.dependencies import get_current_user, require_roles
from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.program_schema import ActivityCreate, CheckinCreate, EnrolmentCreate, PhaseCreate, ProgramCreate, ProgramDetailResponse, ProgramPaginatedResponse, ProgramResponse, ProgramStatusUpdate, ProgramUpdate, ReviewCreate, SurveyCreate
from app.services.program_service import create_program_checkin_service, create_program_service, create_review_service, create_survey_service, delete_program_service, duplicate_program_service, enrol_program_service, get_participant_dashboard_service, get_program_progress_service, get_program_reports_service, get_program_service, get_program_summary_service, get_provider_dashboard_service, get_programs_service, list_checkins_service, list_enrolments_service, update_program_service, update_program_status_service
router=APIRouter(tags=["Programs"])
@router.post("/", response_model=ProgramResponse, status_code=201)
def create_program(data: ProgramCreate, db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))): return create_program_service(db, data)
@router.get("/", response_model=ProgramPaginatedResponse)
def list_programs(search: str|None=Query(None), category: str|None=Query(None), provider: str|None=Query(None, description="provider_id"), tenant_id: UUID|None=Query(None), enterprise_id: UUID|None=Query(None), location_id: UUID|None=Query(None), status_filter: str|None=Query(None, alias="status"), delivery_mode: str|None=Query(None), min_price: str|None=Query(None), max_price: str|None=Query(None), duration: str|None=Query(None, description="duration_weeks"), date_from: str|None=Query(None), date_to: str|None=Query(None), page:int=Query(DEFAULT_PAGE,ge=1), page_size:int=Query(DEFAULT_PAGE_SIZE,ge=1,le=MAX_PAGE_SIZE), db:Session=Depends(get_db)):
    from uuid import UUID as _UUID
    prov_id = None
    try: prov_id = _UUID(provider) if provider else None
    except: prov_id = None
    return get_programs_service(db, search=search, category=category, provider_id=prov_id, tenant_id=tenant_id, enterprise_id=enterprise_id, location_id=location_id, status=status_filter, delivery_mode=delivery_mode, min_price=min_price, max_price=max_price, duration_weeks=duration, date_from=date_from, date_to=date_to, page=page, page_size=page_size)
@router.get("/{program_id}", response_model=ProgramDetailResponse)
def get_program(program_id: UUID=Path(...), db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)): return get_program_service(db, program_id)
@router.put("/{program_id}", response_model=ProgramResponse)
def update_program(data: ProgramUpdate, program_id: UUID=Path(...), db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))): return update_program_service(db, program_id, data)
@router.delete("/{program_id}")
def delete_program(program_id: UUID=Path(...), db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))): delete_program_service(db, program_id); return {"message":"Program deleted"}
@router.post("/{program_id}/duplicate", response_model=ProgramResponse, status_code=201)
def duplicate(program_id: UUID=Path(...), db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))): return duplicate_program_service(db, program_id)
@router.patch("/{program_id}/status", response_model=ProgramResponse)
def update_status(program_id: UUID, payload: ProgramStatusUpdate, db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    if payload.status in ["approved", "published", "completed", "archived", "suspended"] and current_user.get("role") != "admin":
        from fastapi import HTTPException; raise HTTPException(status_code=403, detail="Only admin can set status to approved/published/completed/archived/suspended")
    return update_program_status_service(db, program_id, payload.status)
# Phases / Activities (P6)
@router.get("/{program_id}/phases")
def list_phases(program_id: UUID, db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    p=get_program_service(db, program_id); return p.phases or []
@router.post("/{program_id}/phases", status_code=201)
def add_phase(program_id: UUID, payload: PhaseCreate, db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.program_repo import get_program_by_id; import uuid
    obj=get_program_by_id(db, program_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404,"Program not found")
    phases=list(obj.phases or []); new={"id":str(uuid.uuid4()), **payload.model_dump()}; phases.append(new); obj.phases=phases; db.commit(); return new
@router.put("/{program_id}/phases/{phase_id}")
def update_phase(program_id: UUID, phase_id: str, payload: PhaseCreate, db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.program_repo import get_program_by_id
    obj=get_program_by_id(db, program_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404,"Program not found")
    for ph in obj.phases or []:
        if ph.get("id")==phase_id: ph.update(payload.model_dump(exclude_unset=True)); db.commit(); return ph
    from fastapi import HTTPException; raise HTTPException(404,"Phase not found")
@router.post("/{program_id}/phases/{phase_id}/activities", status_code=201)
def add_activity(program_id: UUID, phase_id: str, payload: ActivityCreate, db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.program_repo import get_program_by_id; import uuid
    obj=get_program_by_id(db, program_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404,"Program not found")
    for ph in obj.phases or []:
        if ph.get("id")==phase_id:
            acts=ph.get("activities",[]); new={"id":str(uuid.uuid4()), **payload.model_dump()}; acts.append(new); ph["activities"]=acts; obj.phases=list(obj.phases); db.commit(); return new
    from fastapi import HTTPException; raise HTTPException(404,"Phase not found")
@router.put("/{program_id}/phases/{phase_id}/activities/{activity_id}")
def update_activity(program_id: UUID, phase_id: str, activity_id: str, payload: ActivityCreate, db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.program_repo import get_program_by_id
    obj=get_program_by_id(db, program_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404,"Program not found")
    for ph in obj.phases or []:
        if ph.get("id")==phase_id:
            for ac in ph.get("activities",[]):
                if ac.get("id")==activity_id: ac.update(payload.model_dump(exclude_unset=True)); db.commit(); return ac
    from fastapi import HTTPException; raise HTTPException(404,"Activity not found")
# Phases - missing delete/reorder
@router.delete("/{program_id}/phases/{phase_id}")
def delete_phase(program_id: UUID, phase_id: str, db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.program_repo import get_program_by_id
    from sqlalchemy.orm.attributes import flag_modified
    obj=get_program_by_id(db, program_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404,"Program not found")
    orig=len(obj.phases or [])
    new=[p for p in (obj.phases or []) if p.get("id")!=phase_id]
    if len(new)==orig: from fastapi import HTTPException; raise HTTPException(404,"Phase not found")
    obj.phases=new; flag_modified(obj,"phases"); db.commit(); return {"message":"Phase deleted"}

@router.delete("/{program_id}/phases/{phase_id}/activities/{activity_id}")
def delete_activity(program_id: UUID, phase_id: str, activity_id: str, db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.program_repo import get_program_by_id
    from sqlalchemy.orm.attributes import flag_modified
    obj=get_program_by_id(db, program_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404,"Program not found")
    for ph in obj.phases or []:
        if ph.get("id")==phase_id:
            acts=[a for a in ph.get("activities",[]) if a.get("id")!=activity_id]
            if len(acts)==len(ph.get("activities",[])): from fastapi import HTTPException; raise HTTPException(404,"Activity not found")
            ph["activities"]=acts; obj.phases=list(obj.phases); flag_modified(obj,"phases"); db.commit(); return {"message":"Activity deleted"}
    from fastapi import HTTPException; raise HTTPException(404,"Phase not found")

@router.post("/{program_id}/phases/reorder")
def reorder_phases(program_id: UUID, payload: dict, db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.program_repo import get_program_by_id
    from sqlalchemy.orm.attributes import flag_modified
    obj=get_program_by_id(db, program_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404,"Program not found")
    order=payload.get("ordered_ids",[])
    mapping={p["id"]: p for p in (obj.phases or []) if p.get("id")}
    obj.phases=[mapping[i] for i in order if i in mapping]
    flag_modified(obj,"phases"); db.commit(); return obj.phases

# Enrol, check-ins, progress, dashboards, surveys, reports
@router.get("/my/enrolments", summary="Participant dashboard — enrolled/active/completed/cancelled")
def my_enrolments(status: str | None = Query(None, description="enrolled|completed|cancelled"), db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.models.program_model import ProgramEnrolment
    email = current_user.get("email")
    if not email:
        from fastapi import HTTPException; raise HTTPException(400, "Email not found in token")
    q = db.query(ProgramEnrolment).filter(ProgramEnrolment.participant_email==email)
    if status: q = q.filter(ProgramEnrolment.status==status)
    rows = q.order_by(ProgramEnrolment.created_at.desc()).all()
    return [{"program_id": str(r.program_id), "status": r.status, "enrolment_id": str(r.id), "created_at": r.created_at.isoformat()} for r in rows]

@router.post("/{program_id}/enrol", status_code=201)
def enrol(program_id: UUID, payload: EnrolmentCreate, db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    return enrol_program_service(db, program_id, payload)
@router.get("/{program_id}/enrolments")
def list_enrolments(program_id: UUID, db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return list_enrolments_service(db, program_id)

@router.get("/{program_id}/content", summary="Secure enrolled content — phases/files gated")
def secure_content(program_id: UUID, db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.models.program_model import ProgramEnrolment
    from app.repository.program_repo import get_program_by_id
    from fastapi import HTTPException
    email = current_user.get("email")
    enrol = db.query(ProgramEnrolment).filter(ProgramEnrolment.program_id==program_id, ProgramEnrolment.participant_email==email).first()
    if not enrol and current_user.get("role") not in ["admin","provider"]:
        raise HTTPException(403, "Enrolled participants only")
    prog = get_program_by_id(db, program_id)
    if not prog: raise HTTPException(404, "Program not found")
    return {"program_id": str(program_id), "phases": prog.phases or [], "goals": prog.goals}

@router.get("/{program_id}/meeting-link", summary="Secure meeting link — enrolled only")
def meeting_link(program_id: UUID, db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.models.program_model import ProgramEnrolment
    from app.repository.program_repo import get_program_by_id
    from fastapi import HTTPException
    email = current_user.get("email")
    enrol = db.query(ProgramEnrolment).filter(ProgramEnrolment.program_id==program_id, ProgramEnrolment.participant_email==email).first()
    if not enrol and current_user.get("role") not in ["admin","provider"]:
        raise HTTPException(403, "Enrolled participants only")
    prog = get_program_by_id(db, program_id)
    if not prog: raise HTTPException(404, "Program not found")
    # aggregate meeting links from phases activities if present
    links = []
    for ph in prog.phases or []:
        for ac in ph.get("activities",[]):
            if ac.get("meeting_link"): links.append(ac.get("meeting_link"))
    return {"program_id": str(program_id), "meeting_links": links}
@router.post("/{program_id}/check-ins", status_code=201)
def checkin(program_id: UUID, payload: CheckinCreate, db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return create_program_checkin_service(db, program_id, payload)
@router.get("/{program_id}/check-ins")
def list_checkins(program_id: UUID, participant_email: str | None = Query(None), phase_id: str | None = Query(None), db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return list_checkins_service(db, program_id, participant_email=participant_email, phase_id=phase_id)
@router.get("/{program_id}/progress")
def progress(program_id: UUID, participant_email: str | None = Query(None), db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    email = participant_email or (current_user.get("email") if current_user else None)
    return get_program_progress_service(db, program_id, participant_email=email)
@router.get("/{program_id}/dashboards/participant")
def dash_participant(program_id: UUID, participant_email: str | None = Query(None), db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    email = participant_email or (current_user.get("email") if current_user else None)
    return get_participant_dashboard_service(db, program_id, participant_email=email)
@router.get("/{program_id}/dashboards/provider")
def dash_provider(program_id: UUID, db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return get_provider_dashboard_service(db, program_id)
@router.put("/{program_id}/goals", summary="Goal & outcome — configurable fields")
def update_goals(program_id: UUID, payload: dict, db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.services.program_service import update_program_goals_service
    return update_program_goals_service(db, program_id, payload.get("goals") or payload)
@router.get("/{program_id}/certificate", summary="Completion certificate / provider acknowledgement")
def get_certificate(program_id: UUID, participant_email: str = Query(...), db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services.program_service import get_program_certificate_service
    return get_program_certificate_service(db, program_id, participant_email)
@router.patch("/{program_id}/enrolments/{enrol_id}/status", summary="Completion/withdrawal/cancellation/extension")
def update_enrol_status(program_id: UUID, enrol_id: UUID, payload: dict, db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.services.program_service import update_enrolment_status_service
    return update_enrolment_status_service(db, program_id, enrol_id, payload.get("status") or payload.get("new_status") or "completed")
@router.post("/{program_id}/surveys", status_code=201)
def create_survey(program_id: UUID, payload: SurveyCreate, db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return create_survey_service(db, program_id, payload)
@router.post("/{program_id}/reviews", status_code=201)
def create_review(program_id: UUID, payload: ReviewCreate, db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    return create_review_service(db, program_id, payload)
@router.get("/{program_id}/reports")
def reports(program_id: UUID, type: str=Query("enrolment", description="enrolment|attendance|engagement|assessment|progress|completion|revenue"), db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return get_program_reports_service(db, program_id, type)

@router.get("/{program_id}/enrolments/export", summary="Export participant & performance data CSV")
def export_enrolments(program_id: UUID, db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from fastapi.responses import StreamingResponse
    import csv, io
    rows = list_enrolments_service(db, program_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id","participant_name","participant_email","status","created_at"])
    for r in rows: writer.writerow([r["id"], r["participant_name"], r["participant_email"], r["status"], r["created_at"]])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=program_{program_id}_enrolments.csv"})

@router.get("/reports/summary")
def summary(enterprise_id: UUID|None=None, db: Session=Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return get_program_summary_service(db, enterprise_id)
