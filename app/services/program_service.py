from uuid import UUID
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models.enterprise_model import Enterprise
from app.models.location_model import EnterpriseLocation
from app.repository.program_repo import create_program, delete_program, get_program_by_id, get_programs, update_program
from app.repository.query_utils import build_pagination_meta
from app.schemas.program_schema import ProgramDetailResponse, ProgramListItemResponse, ProgramPaginatedResponse, ProgramResponse
from app.services.response_mappers import map_program_detail, map_program_list_item, map_program_write

def _validate(db, eid, lid):
    ent=db.query(Enterprise).filter(Enterprise.id==eid, Enterprise.is_deleted.is_(False)).first()
    if not ent: raise HTTPException(404, "Enterprise not found")
    if ent.status in ("draft", "pending", "inactive"):
        raise HTTPException(400, f"Enterprise not approved (status={ent.status}). Programs can only be created under an approved business/profile.")
    if lid:
        loc=db.query(EnterpriseLocation).filter(EnterpriseLocation.id==lid, EnterpriseLocation.enterprise_id==eid, EnterpriseLocation.is_deleted.is_(False)).first()
        if not loc: raise HTTPException(404, "Location not found")

def create_program_service(db, data):
    _validate(db, data.enterprise_id, data.location_id); return ProgramResponse.model_validate(map_program_write(create_program(db, data)))
def get_programs_service(db, **kw):
    items,total=get_programs(db, **kw); return ProgramPaginatedResponse(items=[ProgramListItemResponse.model_validate(map_program_list_item(i)) for i in items], pagination=build_pagination_meta(total, kw.get("page",1), kw.get("page_size",20)))
def get_program_service(db, pid):
    obj=get_program_by_id(db, pid)
    if not obj: raise HTTPException(404, "Program not found")
    return ProgramDetailResponse.model_validate(map_program_detail(obj))
def update_program_service(db, pid, data):
    obj=get_program_by_id(db, pid, include_deleted=True)
    if not obj or obj.is_deleted: raise HTTPException(404, "Program not found")
    return ProgramResponse.model_validate(map_program_write(update_program(db, obj, data)))
def delete_program_service(db, pid):
    obj=get_program_by_id(db, pid)
    if not obj: raise HTTPException(404, "Program not found")
    return delete_program(db, obj)
def duplicate_program_service(db, pid):
    obj=get_program_by_id(db, pid)
    if not obj: raise HTTPException(404, "Program not found")
    payload={c.key: getattr(obj,c.key) for c in obj.__table__.columns if c.key not in ("id","created_at","updated_at")}
    payload["status"]="draft"; payload["is_deleted"]=False
    from app.models.program_model import Program
    clone=Program(**payload); db.add(clone); db.commit(); db.refresh(clone); return ProgramResponse.model_validate(map_program_write(clone))
def update_program_status_service(db, pid, st):
    obj=get_program_by_id(db, pid, include_deleted=True)
    if not obj or obj.is_deleted: raise HTTPException(404, "Program not found")
    VALID = {
        "pending_approval": ["approved", "cancelled"],
        "approved": ["draft", "cancelled", "archived"],
        "draft": ["published", "pending_approval", "cancelled", "archived"],
        "published": ["completed", "cancelled", "suspended", "approved"],
        "suspended": ["published", "cancelled", "archived"],
        "completed": ["archived"], "cancelled": ["draft", "archived"], "archived": [],
    }
    allowed = VALID.get(obj.status, [])
    if allowed and st not in allowed:
        raise HTTPException(400, detail=f"Cannot transition from '{obj.status}' to '{st}'. Allowed: {allowed}")
    obj.status=st; db.commit(); db.refresh(obj); return ProgramResponse.model_validate(map_program_write(obj))

def _get_program_or_404(db, pid):
    obj=get_program_by_id(db, pid)
    if not obj: raise HTTPException(404, "Program not found")
    return obj

def enrol_program_service(db, pid, data):
    from app.models.program_model import ProgramEnrolment
    from datetime import datetime
    prog=_get_program_or_404(db, pid)
    # enrol window: fixed-date vs enrol_anytime
    now=datetime.utcnow()
    if prog.enrol_type != "enrol_anytime":
        if prog.enrolment_start and now < prog.enrolment_start:
            raise HTTPException(400, f"Enrolment not yet open (opens {prog.enrolment_start})")
        if prog.enrolment_end and now > prog.enrolment_end:
            raise HTTPException(400, f"Enrolment closed (closed {prog.enrolment_end})")
    # duplicate check
    exists=db.query(ProgramEnrolment).filter(ProgramEnrolment.program_id==pid, ProgramEnrolment.participant_email==data.participant_email).first()
    if exists:
        raise HTTPException(status_code=400, detail="Already enrolled")
    # capacity / available_seats / waitlist
    if prog.capacity:
        try:
            cap=int(prog.capacity)
            cnt=db.query(ProgramEnrolment).filter(ProgramEnrolment.program_id==pid, ProgramEnrolment.status.in_(["enrolled","active","completed"])).count()
            available=cap - cnt
            if available <= 0:
                # auto waitlist when capacity reached
                raise HTTPException(status_code=400, detail=f"Program at capacity ({cap}). Only {available} seats left. Please join waitlist via POST /{{id}}/waitlist")
        except ValueError:
            pass
    # free vs paid — price empty = free instant enrol
    e=ProgramEnrolment(program_id=pid, participant_name=data.participant_name, participant_email=data.participant_email, status="enrolled")
    db.add(e); db.commit(); db.refresh(e)
    return {"id": str(e.id), "program_id": str(e.program_id), "participant_name": e.participant_name, "participant_email": e.participant_email, "status": e.status, "enrol_type": prog.enrol_type, "delivery_mode": prog.delivery_mode, "available_seats": (int(prog.capacity)-db.query(ProgramEnrolment).filter(ProgramEnrolment.program_id==pid).count() if prog.capacity else None), "created_at": e.created_at.isoformat()}

def list_enrolments_service(db, pid):
    from app.models.program_model import ProgramEnrolment
    _get_program_or_404(db, pid)
    rows=db.query(ProgramEnrolment).filter(ProgramEnrolment.program_id==pid).all()
    return [{"id": str(r.id), "program_id": str(r.program_id), "participant_name": r.participant_name, "participant_email": r.participant_email, "status": r.status, "created_at": r.created_at.isoformat()} for r in rows]

def create_program_checkin_service(db, pid, data):
    from app.models.program_model import ProgramCheckin
    _get_program_or_404(db, pid)
    c=ProgramCheckin(program_id=pid, participant_email=data.participant_email, phase_id=data.phase_id, notes=data.notes)
    db.add(c); db.commit(); db.refresh(c)
    return {"id": str(c.id), "program_id": str(c.program_id), "participant_email": c.participant_email, "phase_id": c.phase_id, "notes": c.notes, "created_at": c.created_at.isoformat()}

def list_checkins_service(db, pid, participant_email=None, phase_id=None):
    from app.models.program_model import ProgramCheckin
    _get_program_or_404(db, pid)
    q=db.query(ProgramCheckin).filter(ProgramCheckin.program_id==pid)
    if participant_email: q=q.filter(ProgramCheckin.participant_email==participant_email)
    if phase_id: q=q.filter(ProgramCheckin.phase_id==phase_id)
    rows=q.all()
    return [{"id": str(r.id), "program_id": str(r.program_id), "participant_email": r.participant_email, "phase_id": r.phase_id, "notes": r.notes, "created_at": r.created_at.isoformat()} for r in rows]

def get_program_progress_service(db, pid, participant_email: str | None = None):
    prog=_get_program_or_404(db, pid)
    phases=prog.phases or []
    total=len(phases)
    # checkins per phase
    from app.models.program_model import ProgramCheckin, ProgramEnrolment, ProgramSurvey
    q=db.query(ProgramCheckin).filter(ProgramCheckin.program_id==pid)
    if participant_email: q=q.filter(ProgramCheckin.participant_email==participant_email)
    checkins=q.all()
    checkin_phases=set(c.phase_id for c in checkins if c.phase_id)
    done=len(checkin_phases)
    overall=round(done/total*100 if total else 0,2)
    stage=[{"stage_number": i+1, "stage_name": p.get("title", f"Stage {i+1}"), "completion_percent": 100 if p.get("id") in checkin_phases else 0, "milestones_achieved": [p.get("id")] if p.get("id") in checkin_phases else []} for i,p in enumerate(phases)]
    milestone=[{"milestone_id": p.get("id"), "milestone_name": p.get("title"), "achieved": p.get("id") in checkin_phases, "achieved_at": next((c.created_at.isoformat() for c in checkins if c.phase_id==p.get("id")), None)} for p in phases]
    # attendance / activities / assessments / missed
    all_activity_ids = [a.get("id") for p in phases for a in p.get("activities",[]) if a.get("id")]
    attendance = {"total_phases": total, "attended": done, "rate": overall}
    activities = {"total_activities": len(all_activity_ids), "completed": done, "ids": all_activity_ids}
    assessments = {"surveys": db.query(ProgramSurvey).filter(ProgramSurvey.program_id==pid).count()}
    missed_tasks = [p.get("id") for p in phases if p.get("id") not in checkin_phases]
    # certificate when complete
    certificate_url = f"/api/v1/programs/{pid}/certificate?participant_email={participant_email}" if overall==100 and total>0 and participant_email else None
    return {"overall": overall, "stage": stage, "milestone": milestone, "milestones_achieved": done==total and total>0, "attendance": attendance, "activities": activities, "assessments": assessments, "missed_tasks": missed_tasks, "certificate_url": certificate_url}

def get_participant_dashboard_service(db, pid, participant_email: str | None = None):
    prog=_get_program_or_404(db, pid)
    from app.models.program_model import ProgramEnrolment, ProgramCheckin
    enrol=None
    if participant_email:
        enrol=db.query(ProgramEnrolment).filter(ProgramEnrolment.program_id==pid, ProgramEnrolment.participant_email==participant_email).first()
    prog_data=get_program_progress_service(db, pid, participant_email)
    phases=prog.phases or []
    recent=db.query(ProgramCheckin).filter(ProgramCheckin.program_id==pid)
    if participant_email: recent=recent.filter(ProgramCheckin.participant_email==participant_email)
    recent=recent.order_by(ProgramCheckin.created_at.desc()).limit(5).all()
    return {"program_id": str(pid), "enrolment_status": enrol.status if enrol else "not_enrolled", "phases": phases, "recent_activities": [{"phase_id": r.phase_id, "created_at": r.created_at.isoformat()} for r in recent], "overall_progress": prog_data["overall"], "stage": prog_data["stage"], "milestone": prog_data["milestone"], "attendance": prog_data["attendance"], "activities": prog_data["activities"], "assessments": prog_data["assessments"], "missed_tasks": prog_data["missed_tasks"], "certificate_url": prog_data["certificate_url"], "goals": prog.goals or {}, "surveys_pending": prog_data["assessments"]["surveys"]}

def get_provider_dashboard_service(db, pid):
    prog=_get_program_or_404(db, pid)
    from app.models.program_model import ProgramEnrolment, ProgramCheckin
    from sqlalchemy import func
    total=db.query(func.count(ProgramEnrolment.id)).filter(ProgramEnrolment.program_id==pid).scalar() or 0
    by_status_rows=db.query(ProgramEnrolment.status, func.count(ProgramEnrolment.id)).filter(ProgramEnrolment.program_id==pid).group_by(ProgramEnrolment.status).all()
    by_status={r[0]: r[1] for r in by_status_rows}
    by_phase_rows=db.query(ProgramCheckin.phase_id, func.count(ProgramCheckin.id)).filter(ProgramCheckin.program_id==pid).group_by(ProgramCheckin.phase_id).all()
    by_phase={r[0] or "unknown": {"count": r[1]} for r in by_phase_rows}
    cap_util=0
    if prog.capacity:
        try:
            cap=int(prog.capacity)
            cap_util=round(total/cap*100 if cap else 0,2)
        except: pass
    recent=db.query(ProgramEnrolment).filter(ProgramEnrolment.program_id==pid).order_by(ProgramEnrolment.created_at.desc()).limit(5).all()
    return {"program_id": str(pid), "total_enrolments": total, "by_status": by_status, "by_phase": by_phase, "capacity_utilization": cap_util, "recent_enrolments": [{"participant_email": r.participant_email, "created_at": r.created_at.isoformat()} for r in recent]}

def create_survey_service(db, pid, data):
    from app.models.program_model import ProgramSurvey
    _get_program_or_404(db, pid)
    s=ProgramSurvey(program_id=pid, title=data.title, description=data.description, questions=data.questions)
    db.add(s); db.commit(); db.refresh(s)
    return {"id": str(s.id), "program_id": str(s.program_id), "title": s.title, "description": s.description, "answers": None, "created_at": s.created_at.isoformat()}

def create_review_service(db, pid, data):
    from app.models.program_model import ProgramReview, ProgramEnrolment
    _get_program_or_404(db, pid)
    # Verified reviews: must be enrolled
    enrolled = db.query(ProgramEnrolment).filter(ProgramEnrolment.program_id==pid, ProgramEnrolment.participant_email==data.participant_email).first()
    if not enrolled:
        raise HTTPException(status_code=400, detail="Verified reviews only — must be enrolled to review")
    r=ProgramReview(program_id=pid, participant_email=data.participant_email, rating=str(data.rating), comment=data.comment)
    db.add(r); db.commit(); db.refresh(r)
    return {"id": str(r.id), "program_id": str(r.program_id), "rating": int(r.rating), "comment": r.comment, "participant_email": r.participant_email, "verified": True, "created_at": r.created_at.isoformat()}

def get_program_reports_service(db, pid, report_type: str = "enrolment"):
    from app.models.program_model import ProgramEnrolment, ProgramCheckin, ProgramReview, ProgramSurvey
    from app.models.training_model import TrainingAssessmentSubmission
    _get_program_or_404(db, pid)
    if report_type=="enrolment":
        rows=db.query(ProgramEnrolment).filter(ProgramEnrolment.program_id==pid).all()
        by_status={}
        for r in rows: by_status[r.status]=by_status.get(r.status,0)+1
        data={"total": len(rows), "by_status": by_status}
    elif report_type in ["attendance","checkin"]:
        rows=db.query(ProgramCheckin).filter(ProgramCheckin.program_id==pid).all()
        by_phase={}
        for r in rows: by_phase[r.phase_id or "unknown"]=by_phase.get(r.phase_id or "unknown",0)+1
        data={"total": len(rows), "by_phase": by_phase, "attendance_rate": round(len(rows)/max(1, db.query(ProgramEnrolment).filter(ProgramEnrolment.program_id==pid).count())*100,2)}
    elif report_type=="progress":
        data=get_program_progress_service(db, pid)
    elif report_type=="engagement":
        enrol_cnt=db.query(ProgramEnrolment).filter(ProgramEnrolment.program_id==pid).count()
        check_cnt=db.query(ProgramCheckin).filter(ProgramCheckin.program_id==pid).count()
        data={"enrolments": enrol_cnt, "checkins": check_cnt, "engagement_rate": round(check_cnt/max(1,enrol_cnt)*100,2)}
    elif report_type=="assessment":
        prog=_get_program_or_404(db, pid)
        survey_cnt=db.query(ProgramSurvey).filter(ProgramSurvey.program_id==pid).count()
        review_cnt=db.query(ProgramReview).filter(ProgramReview.program_id==pid).count()
        data={"surveys": survey_cnt, "reviews": review_cnt, "avg_rating": round(sum(int(r.rating) for r in db.query(ProgramReview).filter(ProgramReview.program_id==pid).all())/max(1,review_cnt),2) if review_cnt else 0}
    elif report_type=="completion":
        data=get_program_progress_service(db, pid)
        data["completion_rate"]=data.get("overall",0)
    elif report_type=="revenue":
        prog=_get_program_or_404(db, pid)
        enrol_cnt=db.query(ProgramEnrolment).filter(ProgramEnrolment.program_id==pid).count()
        try: price=float(prog.price or 0)
        except: price=0
        data={"total_revenue": str(price*enrol_cnt), "currency": prog.currency or "INR", "enrolments": enrol_cnt, "unit_price": str(prog.price or "0")}
    else:
        data={}
    return {"program_id": str(pid), "type": report_type, "data": data}

def update_enrolment_status_service(db, pid, enrol_id, new_status: str):
    from app.models.program_model import ProgramEnrolment
    _get_program_or_404(db, pid)
    allowed = ["completed","withdrawn","cancelled","extended","enrolled","active"]
    if new_status not in allowed:
        raise HTTPException(400, f"Invalid status. Allowed: {allowed}")
    row = db.query(ProgramEnrolment).filter(ProgramEnrolment.id==enrol_id, ProgramEnrolment.program_id==pid).first()
    if not row: raise HTTPException(404, "Enrolment not found")
    row.status=new_status; db.commit(); db.refresh(row)
    return {"id": str(row.id), "program_id": str(row.program_id), "status": row.status, "participant_email": row.participant_email}

def update_program_goals_service(db, pid, goals: dict):
    prog=_get_program_or_404(db, pid)
    prog.goals = goals or {}
    db.commit(); db.refresh(prog)
    return {"program_id": str(pid), "goals": prog.goals}

def get_program_certificate_service(db, pid, participant_email: str):
    prog=_get_program_or_404(db, pid)
    from app.models.program_model import ProgramEnrolment
    enrol=db.query(ProgramEnrolment).filter(ProgramEnrolment.program_id==pid, ProgramEnrolment.participant_email==participant_email).first()
    if not enrol: raise HTTPException(404, "Not enrolled")
    progress=get_program_progress_service(db, pid, participant_email)
    if progress["overall"] < 100:
        raise HTTPException(400, f"Program not completed ({progress['overall']}%). Certificate only on 100%")
    return {"program_id": str(pid), "participant_email": participant_email, "certificate_url": progress["certificate_url"], "overall": progress["overall"], "issued_at": prog.updated_at.isoformat() if prog.updated_at else None, "provider_acknowledgement": f"Acknowledged by provider {prog.provider_id}" if prog.provider_id else "Provider acknowledgement pending"}

def get_program_summary_service(db, enterprise_id=None):
    from sqlalchemy import func
    from app.models.program_model import Program, ProgramEnrolment, ProgramCheckin
    q=db.query(Program).filter(Program.is_deleted.is_(False))
    if enterprise_id: q=q.filter(Program.enterprise_id==enterprise_id)
    status_rows=q.with_entities(Program.status, func.count(Program.id)).group_by(Program.status).all()
    by_status={r[0]: r[1] for r in status_rows}
    cat_rows=q.with_entities(Program.category, func.count(Program.id)).group_by(Program.category).all()
    by_category={r[0]: r[1] for r in cat_rows if r[0]}
    del_rows=q.with_entities(Program.delivery_mode, func.count(Program.id)).group_by(Program.delivery_mode).all()
    by_delivery={r[0]: r[1] for r in del_rows if r[0]}
    total=sum(by_status.values())
    pids=[p.id for p in q.all()]
    total_enrol=0; total_check=0
    if pids:
        total_enrol=db.query(func.count(ProgramEnrolment.id)).filter(ProgramEnrolment.program_id.in_(pids)).scalar() or 0
        total_check=db.query(func.count(ProgramCheckin.id)).filter(ProgramCheckin.program_id.in_(pids)).scalar() or 0
    return {"total_programs": total, "by_status": by_status, "by_category": by_category, "by_delivery_mode": by_delivery, "total_enrolments": total_enrol, "total_checkins": total_check}
