from uuid import UUID
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.models.enterprise_model import Enterprise
from app.models.location_model import EnterpriseLocation
from app.repository.training_repo import create_training, delete_training, get_training_by_id, get_trainings, update_training
from app.repository.query_utils import build_pagination_meta
from app.schemas.training_schema import TrainingDetailResponse, TrainingListItemResponse, TrainingPaginatedResponse, TrainingResponse
from app.services.response_mappers import map_training_detail, map_training_list_item, map_training_write

def _validate(db: Session, eid: UUID, lid: UUID | None, current_user: dict | None = None):
    ent = db.query(Enterprise).filter(Enterprise.id==eid, Enterprise.is_deleted.is_(False)).first()
    if not ent: raise HTTPException(status_code=404, detail="Enterprise not found")
    if current_user and current_user.get("role") not in ("admin", "super_admin"):
        user_tid = current_user.get("tenant_id")
        if user_tid and str(ent.tenant_id) != str(user_tid):
            raise HTTPException(status_code=403, detail="Not authorized for this enterprise/tenant")
    if ent.status in ("draft", "pending", "inactive"):
        raise HTTPException(status_code=400, detail=f"Enterprise not approved (status={ent.status}). Trainings can only be created under an approved business/profile.")
    if lid:
        loc = db.query(EnterpriseLocation).filter(EnterpriseLocation.id==lid, EnterpriseLocation.enterprise_id==eid, EnterpriseLocation.is_deleted.is_(False)).first()
        if not loc: raise HTTPException(status_code=404, detail="Location not found for this enterprise")

def create_training_service(db: Session, data, current_user: dict | None = None):
    _validate(db, data.enterprise_id, data.location_id, current_user)
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
        "pending_approval": ["approved", "cancelled"],
        "approved": ["draft", "published", "cancelled", "archived"],
        "draft": ["published", "pending_approval", "cancelled", "archived"],
        "published": ["completed", "cancelled", "suspended", "approved", "archived"],
        "suspended": ["published", "cancelled", "archived"],
        "completed": ["archived"], "cancelled": ["draft", "archived"], "archived": [],
    }
    allowed = VALID.get(obj.status, [])
    if st not in allowed:
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
    if _check_access_expiry(db, tid, participant_email):
        raise HTTPException(status_code=403, detail="Access expired")
    t = _get_training_or_404(db, tid)
    assessments = t.assessments or []
    target = next((a for a in assessments if str(a.get("id")) == str(aid)), None)
    if not target:
        raise HTTPException(status_code=404, detail="Assessment not found")
    # attempt limit + time limit
    attempt_limit = int(target.get("attempt_limit") or target.get("attempts_allowed") or 999)
    cnt = db.query(TrainingAssessmentSubmission).filter(TrainingAssessmentSubmission.training_id==tid, TrainingAssessmentSubmission.assessment_id==str(aid), TrainingAssessmentSubmission.participant_email==participant_email).count()
    if cnt >= attempt_limit:
        raise HTTPException(400, f"Attempt limit reached ({attempt_limit})")
    if target.get("time_limit_minutes"):
        # enforce via started_at if provided in payload else ignore
        pass
    # scheduled publication
    if target.get("publish_at"):
        from datetime import datetime
        try:
            pub=datetime.fromisoformat(str(target.get("publish_at")))
            if datetime.utcnow() < pub:
                raise HTTPException(400, f"Results scheduled for {pub.isoformat()}")
        except HTTPException: raise
        except: pass
    questions = target.get("questions", [])
    answers = payload.answers if hasattr(payload, "answers") else payload.get("answers", []) if isinstance(payload, dict) else []
    # Build lookup
    qmap = {str(q.get("id")): q for q in questions}
    total = sum(int(q.get("points", 1)) for q in questions) or len(questions)
    score = 0
    needs_manual = False
    for ans in answers:
        qid = str(ans.get("question_id") or ans.get("id") or "")
        given = str(ans.get("answer", "")).strip().lower()
        q = qmap.get(qid)
        if not q:
            continue
        qtype = q.get("question_type") or "mcq"
        if qtype in ["short_answer","essay"]:
            needs_manual = True
            continue
        if qtype == "multiple_select":
            correct = str(q.get("correct_answer", "")).strip().lower()
            given_set = set([s.strip() for s in given.split(",") if s.strip()])
            correct_set = set([s.strip() for s in correct.split(",") if s.strip()])
            if given_set == correct_set and given_set:
                score += int(q.get("points", 1))
        else:
            correct = str(q.get("correct_answer", "")).strip().lower()
            if given and correct and given == correct:
                score += int(q.get("points", 1))
    passing = int(target.get("passing_score") or target.get("pass_mark") or (total * 0.6 if total else 0))
    passed = score >= passing and not needs_manual
    sub = TrainingAssessmentSubmission(training_id=tid, assessment_id=str(aid), participant_email=participant_email, answers=answers, score=str(score), passed=passed)
    db.add(sub); db.commit(); db.refresh(sub)
    publication = target.get("publication") or target.get("result_publication") or "immediate"
    return {"score": score, "passed": passed, "total_points": total, "feedback": "Passed" if passed else ("Pending manual evaluation" if needs_manual else "Failed"), "assessment_id": str(aid), "submission_id": str(sub.id), "publication": publication, "needs_manual": needs_manual}

def grade_assessment_manual_service(db: Session, tid: UUID, aid: str, submission_id: str, grade: int, feedback: str | None = None):
    from app.models.training_model import TrainingAssessmentSubmission
    t=_get_training_or_404(db, tid)
    sub=db.query(TrainingAssessmentSubmission).filter(TrainingAssessmentSubmission.id==submission_id, TrainingAssessmentSubmission.training_id==tid).first()
    if not sub: raise HTTPException(404, "Submission not found")
    # recalc passed with manual grade
    assessments=t.assessments or []
    target=next((a for a in assessments if str(a.get("id"))==str(aid)), None)
    total=sum(int(q.get("points",1)) for q in (target.get("questions",[]) if target else [])) or 1
    passing=int(target.get("passing_score") or target.get("pass_mark") or total*0.6) if target else total*0.6
    sub.score=str(grade); sub.passed=grade>=passing; db.commit(); db.refresh(sub)
    return {"submission_id": str(sub.id), "score": grade, "passed": sub.passed, "total_points": total, "feedback": feedback, "explanation": "Manual evaluation completed"}

def get_assessment_result_service(db: Session, tid: UUID, aid: str, submission_id: str, current_user: dict = None):
    from app.models.training_model import TrainingAssessmentSubmission
    t=_get_training_or_404(db, tid)
    sub=db.query(TrainingAssessmentSubmission).filter(TrainingAssessmentSubmission.id==submission_id).first()
    if not sub: raise HTTPException(404, "Submission not found")
    if current_user and current_user.get("role") not in ("admin","provider") and sub.participant_email != current_user.get("email"):
        raise HTTPException(403, "Not authorized to view this submission")
    assessments=t.assessments or []
    target=next((a for a in assessments if str(a.get("id"))==str(aid)), None)
    questions=target.get("questions",[]) if target else []
    # attach explanations
    review=[]
    qmap={str(q.get("id")): q for q in questions}
    for ans in sub.answers or []:
        qid=str(ans.get("question_id") or ans.get("id") or "")
        q=qmap.get(qid, {})
        review.append({"question_id": qid, "question_text": q.get("question_text"), "given": ans.get("answer"), "correct": q.get("correct_answer"), "explanation": q.get("explanation"), "points": q.get("points",1)})
    return {"submission_id": str(sub.id), "assessment_id": str(aid), "score": sub.score, "passed": sub.passed, "review": review, "level": target.get("level") if target else None}

def get_question_bank_service(db: Session, tid: UUID):
    t=_get_training_or_404(db, tid)
    bank=[]
    for a in t.assessments or []:
        for q in a.get("questions",[]):
            if q.get("reusable"):
                bank.append({**q, "source_assessment": a.get("id")})
    return bank

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
    if _check_access_expiry(db, tid, participant_email):
        raise HTTPException(status_code=403, detail="Access expired")
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

def _check_access_expiry(db: Session, tid: UUID, participant_email: str | None):
    if not participant_email:
        return None
    from app.models.training_model import TrainingEnrolment
    enrol = db.query(TrainingEnrolment).filter(TrainingEnrolment.training_id==tid, TrainingEnrolment.participant_email==participant_email).first()
    if enrol and getattr(enrol, "access_expires_at", None):
        from datetime import datetime
        if datetime.utcnow() > enrol.access_expires_at:
            return enrol.access_expires_at
    return None

def get_training_progress_service(db: Session, tid: UUID, participant_email: str | None = None):
    t = _get_training_or_404(db, tid)
    sections = t.sections or []
    total_sections = len(sections)
    total_lessons = sum(len(s.get("lessons", [])) for s in sections)
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
    sections_done = len(completed_sections)
    lessons_done = len(completed_lessons)
    overall = round((lessons_done / total_lessons * 100) if total_lessons else (sections_done / total_sections * 100 if total_sections else 0), 2)
    sections_detail = [{"section_id": s.get("id"), "section_title": s.get("title"), "lessons_done": sum(1 for l in s.get("lessons", []) if l.get("id") in completed_lessons), "total_lessons": len(s.get("lessons", []))} for s in sections]
    lessons_detail = []
    for s in sections:
        for l in s.get("lessons", []):
            lessons_detail.append({"lesson_id": l.get("id"), "lesson_title": l.get("title"), "is_completed": l.get("id") in completed_lessons})
    # access expiry enforcement
    expires_at = _check_access_expiry(db, tid, participant_email)
    expired = expires_at is not None
    return {"overall_percent": overall, "sections_done": sections_done, "total_sections": total_sections, "lessons_done": lessons_done, "total_lessons": total_lessons, "certificate_url": certificate_url, "sections_detail": sections_detail, "lessons_detail": lessons_detail, "expired": expired, "status": "expired" if expired else "active", "access_expires_at": expires_at.isoformat() if expires_at else None}

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

def grade_assignment_service(db: Session, tid: UUID, aid: str, submission_id: str, grade: str, feedback: str | None = None):
    from app.models.training_model import TrainingAssignmentSubmission
    _get_training_or_404(db, tid)
    sub=db.query(TrainingAssignmentSubmission).filter(TrainingAssignmentSubmission.id==submission_id, TrainingAssignmentSubmission.training_id==tid).first()
    if not sub: raise HTTPException(404, "Submission not found")
    sub.grade=str(grade); sub.feedback=feedback; db.commit(); db.refresh(sub)
    return {"id": str(sub.id), "grade": sub.grade, "feedback": sub.feedback, "resubmission_allowed": True}

def complete_lesson_service(db: Session, tid: UUID, lesson_id: str, participant_email: str):
    from app.models.training_model import TrainingProgress
    from datetime import datetime
    t=_get_training_or_404(db, tid)
    prog=db.query(TrainingProgress).filter(TrainingProgress.training_id==tid, TrainingProgress.participant_email==participant_email).first()
    if not prog:
        prog=TrainingProgress(training_id=tid, participant_email=participant_email, sections_completed=[], lessons_completed=[], overall_percent="0")
        db.add(prog); db.commit(); db.refresh(prog)
    lessons=set(prog.lessons_completed or [])
    lessons.add(lesson_id)
    prog.lessons_completed=list(lessons)
    # mandatory check — count mandatory lessons
    all_lessons=[]
    mandatory_ids=set()
    for s in t.sections or []:
        for l in s.get("lessons",[]):
            all_lessons.append(l.get("id"))
            if l.get("is_mandatory") or l.get("completion_rule")=="mandatory":
                mandatory_ids.add(l.get("id"))
    # overall + mandatory rule
    total=len(all_lessons) or 1
    mandatory_done=len(mandatory_ids.intersection(lessons))
    mandatory_total=len(mandatory_ids)
    overall=round(len(lessons)/total*100,2)
    prog.overall_percent=str(overall)
    prog.last_accessed_at=datetime.utcnow()
    # completion when 100% or mandatory done
    if overall==100 or (mandatory_total and mandatory_done==mandatory_total):
        prog.completed_at=datetime.utcnow()
        prog.certificate_url=f"/api/v1/trainings/{tid}/certificate?participant_email={participant_email}"
    db.commit(); db.refresh(prog)
    # last completed for resume
    return {"lesson_id": lesson_id, "overall_percent": overall, "lessons_done": len(lessons), "total_lessons": total, "mandatory_done": mandatory_done, "mandatory_total": mandatory_total, "completed_at": prog.completed_at.isoformat() if prog.completed_at else None, "certificate_url": prog.certificate_url, "resume_lesson": lesson_id}

def record_live_attendance_service(db: Session, tid: UUID, session_id: str, participant_email: str):
    from app.models.training_model import TrainingProgress
    from datetime import datetime
    # reuse complete_lesson as attendance
    return complete_lesson_service(db, tid, f"live:{session_id}", participant_email)

def get_certificate_service(db: Session, tid: UUID, participant_email: str):
    from app.models.training_model import TrainingProgress
    prog=db.query(TrainingProgress).filter(TrainingProgress.training_id==tid, TrainingProgress.participant_email==participant_email).first()
    if not prog or not prog.certificate_url:
        raise HTTPException(400, "Certificate not yet available — complete mandatory lessons")
    return {"training_id": str(tid), "participant_email": participant_email, "certificate_url": prog.certificate_url, "completed_at": prog.completed_at.isoformat() if prog.completed_at else None, "overall_percent": prog.overall_percent}

def create_training_announcement_service(db: Session, tid: UUID, data, current_user: dict | None = None):
    _get_training_or_404(db, tid)
    import uuid as _uuid, datetime
    return {"id": str(_uuid.uuid4()), "training_id": str(tid), "title": data.title, "message": data.message, "sent_at": datetime.datetime.utcnow().isoformat(), "channel": data.channel}

def create_training_enrol_service(db: Session, tid: UUID, payload: dict, coupon_code: str | None = None):
    from app.models.training_model import TrainingEnrolment
    from datetime import datetime, timedelta
    t = _get_training_or_404(db, tid)
    # coupon validation
    if t.coupon_code and coupon_code != t.coupon_code:
        raise HTTPException(status_code=400, detail="Invalid coupon code")
    # if promo price exists and coupon not needed, keep
    # capacity
    if t.capacity:
        try:
            cap = int(t.capacity)
            cnt = db.query(TrainingEnrolment).filter(TrainingEnrolment.training_id==tid, TrainingEnrolment.status.in_(["enrolled","pending_approval"])).count()
            if cnt >= cap:
                raise HTTPException(status_code=400, detail=f"Training at capacity ({cap})")
        except ValueError:
            pass
    # determine status
    status = "pending_approval" if getattr(t, "requires_approval", False) else "enrolled"
    # access expiry
    expires = None
    if getattr(t, "access_duration_days", None):
        try:
            days = int(t.access_duration_days)
            expires = datetime.utcnow() + timedelta(days=days)
        except Exception:
            pass
    e = TrainingEnrolment(training_id=tid, participant_name=payload.get("participant_name","User"), participant_email=payload.get("participant_email","user@example.com"), group_enrol=payload.get("group_enrol", False), status=status, coupon_code=coupon_code, access_expires_at=expires)
    db.add(e); db.commit(); db.refresh(e)
    # group enrolment — create additional members if provided
    if payload.get("group_members"):
        for m in payload.get("group_members") or []:
            try:
                name=m.get("name") or m.get("participant_name") or payload.get("participant_name")
                email=m.get("email") or m.get("participant_email")
                if not email or email==payload.get("participant_email"):
                    continue
                extra=TrainingEnrolment(training_id=tid, participant_name=name, participant_email=email, group_enrol=True, status=status, coupon_code=coupon_code, access_expires_at=expires)
                db.add(extra)
            except: pass
        db.commit()
    # enrolment confirmation (in_app/push/email/sms stub)
    try:
        from app.services.notification_triggers import _safe_notify
        _safe_notify(db, f"training:{tid}", "training_enrolment_confirmation", {"training_id": str(tid), "status": status})
    except: pass
    return e

def cancel_training_enrol_service(db: Session, tid: UUID, enrol_id: UUID, participant_email: str | None = None):
    from app.models.training_model import TrainingEnrolment
    q=db.query(TrainingEnrolment).filter(TrainingEnrolment.id==enrol_id, TrainingEnrolment.training_id==tid)
    if participant_email: q=q.filter(TrainingEnrolment.participant_email==participant_email)
    e=q.first()
    if not e: raise HTTPException(404, "Enrolment not found")
    if e.status=="cancelled": return e
    e.status="cancelled"; db.commit(); db.refresh(e)
    try:
        from app.services.notification_triggers import _safe_notify
        _safe_notify(db, f"training:{tid}", "training_enrolment_cancelled", {"training_id": str(tid)})
    except: pass
    return e

def approve_training_enrol_service(db: Session, tid: UUID, enrol_id: UUID, action: str):
    from app.models.training_model import TrainingEnrolment
    e = db.query(TrainingEnrolment).filter(TrainingEnrolment.id==enrol_id, TrainingEnrolment.training_id==tid).first()
    if not e:
        raise HTTPException(status_code=404, detail="Enrolment not found")
    if action == "approve":
        e.status = "enrolled"
    elif action == "reject":
        e.status = "cancelled"
    else:
        raise HTTPException(status_code=400, detail="action must be approve|reject")
    db.commit(); db.refresh(e)
    return e

def create_training_checkout_service(db: Session, tid: UUID, payload):
    from app.models.training_model import TrainingOrder, TrainingEnrolment
    t = _get_training_or_404(db, tid)
    # coupon check
    coupon = getattr(payload, "coupon_code", None) or payload.get("coupon_code") if isinstance(payload, dict) else None
    if t.coupon_code and coupon != t.coupon_code:
        raise HTTPException(status_code=400, detail="Invalid coupon code")
    # price - use promo_price if coupon valid
    price = t.promo_price if (coupon and t.promo_price) else t.price or "0"
    try:
        total = float(price or "0") * int(getattr(payload, "quantity", 1) or 1)
        amount = str(total)
    except Exception:
        amount = str(price)
    currency = t.currency or "INR"
    order = TrainingOrder(training_id=tid, participant_name=payload.participant_name if hasattr(payload, "participant_name") else payload.get("participant_name"), participant_email=payload.participant_email if hasattr(payload, "participant_email") else payload.get("participant_email"), quantity=str(getattr(payload, "quantity", 1)), amount=amount, currency=currency, payment_status="confirmed", status="confirmed", coupon_code=coupon)
    db.add(order); db.commit(); db.refresh(order)
    # also create enrolment if not exists
    try:
        enrol = TrainingEnrolment(training_id=tid, participant_name=order.participant_name, participant_email=order.participant_email, status="pending_approval" if getattr(t, "requires_approval", False) else "enrolled", coupon_code=coupon)
        # access expiry
        if getattr(t, "access_duration_days", None):
            from datetime import datetime, timedelta
            try:
                enrol.access_expires_at = datetime.utcnow() + timedelta(days=int(t.access_duration_days))
            except: pass
        db.add(enrol); db.commit()
    except Exception:
        pass
    return order

def get_training_orders_service(db: Session, tid: UUID):
    from app.models.training_model import TrainingOrder
    _get_training_or_404(db, tid)
    return db.query(TrainingOrder).filter(TrainingOrder.training_id==tid).order_by(TrainingOrder.created_at.desc()).all()


# ---- Training Order Status & Refund ----

def update_training_order_status_service(db: Session, tid: UUID, order_id: UUID, payload):
    from app.models.training_model import TrainingOrder
    VALID_TRANSITIONS = {
        "confirmed": ["cancelled", "completed"],
        "refund_requested": ["refunded", "cancelled"],
        "cancelled": [],
        "refunded": [],
        "completed": [],
    }
    order = db.query(TrainingOrder).filter(TrainingOrder.id == order_id, TrainingOrder.training_id == tid).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    new_status = payload.status
    allowed = VALID_TRANSITIONS.get(order.status, [])
    if new_status not in allowed:
        raise HTTPException(status_code=400, detail=f"Cannot transition from '{order.status}' to '{new_status}'. Allowed: {allowed}")
    order.status = new_status
    db.commit()
    db.refresh(order)
    return {"id": str(order.id), "status": order.status, "message": f"Order status updated to '{new_status}'"}


def request_training_refund_service(db: Session, tid: UUID, order_id: UUID, payload):
    from app.models.training_model import TrainingOrder
    order = db.query(TrainingOrder).filter(TrainingOrder.id == order_id, TrainingOrder.training_id == tid).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status in ("cancelled", "refunded"):
        raise HTTPException(status_code=400, detail=f"Order already {order.status}")
    order.status = "refund_requested"
    db.commit()
    db.refresh(order)
    return {"id": str(order.id), "status": order.status, "message": "Refund requested"}


def approve_training_refund_service(db: Session, tid: UUID, order_id: UUID, payload):
    from app.models.training_model import TrainingOrder
    order = db.query(TrainingOrder).filter(TrainingOrder.id == order_id, TrainingOrder.training_id == tid).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != "refund_requested":
        raise HTTPException(status_code=400, detail=f"Order is not in refund_requested state (current: {order.status})")
    action = payload.action
    if action == "approve":
        order.status = "refunded"
        order.payment_status = "refunded"
        message = "Refund approved"
    elif action == "reject":
        order.status = "confirmed"
        order.payment_status = "confirmed"
        message = "Refund rejected — order restored to confirmed"
    else:
        raise HTTPException(status_code=400, detail="action must be approve|reject")
    db.commit()
    db.refresh(order)
    return {"id": str(order.id), "status": order.status, "payment_status": order.payment_status, "message": message}
