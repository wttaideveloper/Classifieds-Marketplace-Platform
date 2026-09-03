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
        "pending_approval": ["approved", "cancelled", "rejected"],
        "approved": ["draft", "published", "unpublished", "cancelled", "archived"],
        "draft": ["published", "pending_approval", "cancelled", "archived"],
        "published": ["completed", "cancelled", "suspended", "unpublished", "archived"],
        "unpublished": ["published", "draft", "cancelled", "archived"],
        "suspended": ["published", "unpublished", "cancelled", "archived"],
        "completed": ["archived"],
        "cancelled": ["draft", "archived"],
        "rejected": ["draft", "pending_approval", "archived"],
        "archived": [],
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


def _find_lesson(training, section_id: str, lesson_id: str):
    for section in training.sections or []:
        if section.get("id") == section_id:
            for lesson in section.get("lessons", []):
                if lesson.get("id") == lesson_id:
                    return section, lesson
    return None, None


def _all_lesson_ids(training) -> set[str]:
    return {
        str(l.get("id"))
        for s in training.sections or []
        for l in s.get("lessons", [])
        if l.get("id")
    }


def _enforce_enrolment_window(training) -> None:
    from datetime import datetime
    now = datetime.utcnow()
    if training.enrolment_start and now < training.enrolment_start:
        raise HTTPException(status_code=400, detail=f"Enrolment not yet open (opens {training.enrolment_start.isoformat()})")
    if training.enrolment_end and now > training.enrolment_end:
        raise HTTPException(status_code=400, detail=f"Enrolment closed (closed {training.enrolment_end.isoformat()})")


def _get_enrolment(db: Session, tid: UUID, participant_email: str):
    from app.models.training_model import TrainingEnrolment
    return db.query(TrainingEnrolment).filter(
        TrainingEnrolment.training_id == tid,
        TrainingEnrolment.participant_email == participant_email,
    ).first()


def _lesson_is_accessible(lesson: dict, completed_lessons: set[str], enrolment, training) -> tuple[bool, str | None]:
    from datetime import datetime, timedelta
    if lesson.get("is_draft"):
        return False, "Lesson is in draft mode"
    prereqs = lesson.get("prerequisites") or []
    for pid in prereqs:
        if str(pid) not in completed_lessons:
            return False, f"Prerequisite lesson {pid} not completed"
    rule = lesson.get("release_rule") or {}
    mode = rule.get("mode")
    if mode == "date":
        try:
            release_at = datetime.fromisoformat(str(rule.get("date")))
            if datetime.utcnow() < release_at:
                return False, f"Lesson releases on {release_at.isoformat()}"
        except (TypeError, ValueError):
            pass
    elif mode == "enrolment_day" and enrolment:
        try:
            days = int(rule.get("days") or 0)
            unlock_at = enrolment.created_at + timedelta(days=days)
            if datetime.utcnow() < unlock_at:
                return False, f"Lesson unlocks on day {days} after enrolment ({unlock_at.isoformat()})"
        except (TypeError, ValueError):
            pass
    elif mode == "previous_lesson":
        prev_id = rule.get("lesson_id")
        if prev_id and str(prev_id) not in completed_lessons:
            return False, f"Previous lesson {prev_id} must be completed first"
    return True, None


def _promote_waitlist(db: Session, tid: UUID) -> dict | None:
    from app.models.training_model import TrainingEnrolment, TrainingWaitlist
    training = _get_training_or_404(db, tid)
    if not training.capacity:
        return None
    try:
        cap = int(training.capacity)
    except ValueError:
        return None
    enrolled = db.query(TrainingEnrolment).filter(
        TrainingEnrolment.training_id == tid,
        TrainingEnrolment.status.in_(["enrolled", "pending_approval", "active"]),
    ).count()
    if enrolled >= cap:
        return None
    next_wait = (
        db.query(TrainingWaitlist)
        .filter(TrainingWaitlist.training_id == tid)
        .order_by(TrainingWaitlist.created_at.asc())
        .first()
    )
    if not next_wait:
        return None
    status = "pending_approval" if getattr(training, "requires_approval", False) else "enrolled"
    promoted = TrainingEnrolment(
        training_id=tid,
        participant_name=next_wait.participant_name,
        participant_email=next_wait.participant_email,
        status=status,
    )
    db.add(promoted)
    db.delete(next_wait)
    db.commit()
    db.refresh(promoted)
    return {"enrolment_id": str(promoted.id), "participant_email": promoted.participant_email, "status": promoted.status}


def _append_moderation(db: Session, training, action: str, reason: str | None, actor: dict | None):
    from datetime import datetime
    from sqlalchemy.orm.attributes import flag_modified
    history = list(getattr(training, "moderation_history", None) or [])
    history.append({
        "action": action,
        "reason": reason,
        "actor_email": (actor or {}).get("email"),
        "actor_role": (actor or {}).get("role"),
        "at": datetime.utcnow().isoformat(),
    })
    training.moderation_history = history
    flag_modified(training, "moderation_history")

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
        started_at = None
        if hasattr(payload, "started_at"):
            started_at = payload.started_at
        elif isinstance(payload, dict):
            started_at = payload.get("started_at")
        if started_at:
            from datetime import datetime, timedelta
            try:
                start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
                limit = int(target.get("time_limit_minutes"))
                if datetime.utcnow() > start + timedelta(minutes=limit):
                    raise HTTPException(status_code=400, detail=f"Time limit exceeded ({limit} minutes)")
            except HTTPException:
                raise
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Invalid started_at for timed assessment")
        else:
            raise HTTPException(status_code=400, detail="started_at required for timed assessments")
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
    from datetime import datetime
    due_date = target.get("due_date")
    allow_late = bool(target.get("allow_late_submissions"))
    if due_date and not allow_late:
        try:
            due = datetime.fromisoformat(str(due_date).replace("Z", "+00:00"))
            if datetime.utcnow() > due:
                raise HTTPException(status_code=400, detail="Due date has passed — late submissions not allowed")
        except HTTPException:
            raise
        except (TypeError, ValueError):
            pass
    accepted = target.get("accepted_file_types") or []
    file_url = payload.file_url if hasattr(payload, "file_url") else payload.get("file_url") if isinstance(payload, dict) else None
    if accepted and file_url:
        import os
        ext = os.path.splitext(str(file_url))[-1].lower()
        normalized = {str(t).lower() if str(t).startswith(".") else f".{str(t).lower()}" for t in accepted}
        if ext and ext not in normalized:
            raise HTTPException(status_code=400, detail=f"File type {ext} not allowed. Accepted: {sorted(normalized)}")
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
    t = _get_training_or_404(db, tid)
    enrol = _get_enrolment(db, tid, participant_email)
    if not enrol or enrol.status not in ("enrolled", "active", "completed"):
        raise HTTPException(status_code=403, detail="Active enrolment required")
    prog = db.query(TrainingProgress).filter(
        TrainingProgress.training_id == tid,
        TrainingProgress.participant_email == participant_email,
    ).first()
    if not prog:
        prog = TrainingProgress(
            training_id=tid,
            participant_email=participant_email,
            sections_completed=[],
            lessons_completed=[],
            overall_percent="0",
        )
        db.add(prog)
        db.commit()
        db.refresh(prog)
    lessons = set(prog.lessons_completed or [])
    completed_sections = set(prog.sections_completed or [])
    target_lesson = None
    target_section_id = None
    for section in t.sections or []:
        for lesson in section.get("lessons", []):
            if lesson.get("id") == lesson_id:
                target_lesson = lesson
                target_section_id = section.get("id")
                break
        if target_lesson:
            break
    if not target_lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    accessible, reason = _lesson_is_accessible(target_lesson, lessons, enrol, t)
    if not accessible:
        raise HTTPException(status_code=403, detail=reason or "Lesson not accessible")
    lessons.add(lesson_id)
    prog.lessons_completed = list(lessons)
    if target_section_id:
        section_lessons = [
            l.get("id")
            for s in t.sections or []
            if s.get("id") == target_section_id
            for l in s.get("lessons", [])
            if l.get("id")
        ]
        if section_lessons and all(lid in lessons for lid in section_lessons):
            completed_sections.add(target_section_id)
            prog.sections_completed = list(completed_sections)
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
    from app.models.training_model import TrainingLiveSession
    from datetime import datetime
    session = db.query(TrainingLiveSession).filter(
        TrainingLiveSession.training_id == tid,
        TrainingLiveSession.id == session_id,
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Live session not found")
    attendance = list(session.attendance or [])
    if not any(a.get("participant_email") == participant_email for a in attendance):
        attendance.append({
            "participant_email": participant_email,
            "recorded_at": datetime.utcnow().isoformat(),
        })
        session.attendance = attendance
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(session, "attendance")
        db.commit()
    complete_lesson_service(db, tid, f"live:{session_id}", participant_email)
    return {"session_id": session_id, "participant_email": participant_email, "recorded_at": attendance[-1]["recorded_at"]}

def get_certificate_service(db: Session, tid: UUID, participant_email: str):
    from app.models.training_model import TrainingProgress
    prog=db.query(TrainingProgress).filter(TrainingProgress.training_id==tid, TrainingProgress.participant_email==participant_email).first()
    if not prog or not prog.certificate_url:
        raise HTTPException(400, "Certificate not yet available — complete mandatory lessons")
    return {"training_id": str(tid), "participant_email": participant_email, "certificate_url": prog.certificate_url, "completed_at": prog.completed_at.isoformat() if prog.completed_at else None, "overall_percent": prog.overall_percent}

def create_training_announcement_service(db: Session, tid: UUID, data, current_user: dict | None = None):
    import uuid as _uuid
    from datetime import datetime
    from sqlalchemy.orm.attributes import flag_modified
    training = _get_training_or_404(db, tid)
    entry = {
        "id": str(_uuid.uuid4()),
        "training_id": str(tid),
        "title": data.title,
        "message": data.message,
        "channel": data.channel,
        "author": (current_user or {}).get("email"),
        "sent_at": datetime.utcnow().isoformat(),
    }
    announcements = list(getattr(training, "announcements", None) or [])
    announcements.append(entry)
    training.announcements = announcements
    flag_modified(training, "announcements")
    db.commit()
    return entry


def list_training_announcements_service(db: Session, tid: UUID):
    training = _get_training_or_404(db, tid)
    return list(getattr(training, "announcements", None) or [])


def get_live_attendance_service(db: Session, tid: UUID, session_id: str):
    from app.models.training_model import TrainingLiveSession
    session = db.query(TrainingLiveSession).filter(
        TrainingLiveSession.training_id == tid,
        TrainingLiveSession.id == session_id,
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Live session not found")
    return {
        "session_id": str(session.id),
        "title": session.title,
        "attendance": session.attendance or [],
        "count": len(session.attendance or []),
    }


def export_live_attendance_service(db: Session, tid: UUID, session_id: str):
    data = get_live_attendance_service(db, tid, session_id)
    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["participant_email", "recorded_at"])
    for row in data["attendance"]:
        writer.writerow([row.get("participant_email"), row.get("recorded_at")])
    output.seek(0)
    return output.getvalue(), data["session_id"]

def create_training_enrol_service(db: Session, tid: UUID, payload: dict, coupon_code: str | None = None):
    from app.models.training_model import TrainingEnrolment
    from datetime import datetime, timedelta
    t = _get_training_or_404(db, tid)
    if t.status not in ("published", "approved"):
        raise HTTPException(status_code=400, detail=f"Training not open for enrolment (status={t.status})")
    _enforce_enrolment_window(t)
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
    e.status = "cancelled"
    db.commit()
    db.refresh(e)
    promoted = _promote_waitlist(db, tid)
    try:
        from app.services.notification_triggers import _safe_notify
        _safe_notify(db, f"training:{tid}", "training_enrolment_cancelled", {"training_id": str(tid)})
    except Exception:
        pass
    result = {"id": str(e.id), "status": e.status}
    if promoted:
        result["waitlist_promoted"] = promoted
    return result

def approve_training_enrol_service(db: Session, tid: UUID, enrol_id: UUID, action: str, reason: str | None = None, current_user: dict | None = None):
    from app.models.training_model import TrainingEnrolment
    training = _get_training_or_404(db, tid)
    e = db.query(TrainingEnrolment).filter(TrainingEnrolment.id == enrol_id, TrainingEnrolment.training_id == tid).first()
    if not e:
        raise HTTPException(status_code=404, detail="Enrolment not found")
    if action == "approve":
        e.status = "enrolled"
        _append_moderation(db, training, "enrolment_approved", reason, current_user)
    elif action == "reject":
        e.status = "cancelled"
        _append_moderation(db, training, "enrolment_rejected", reason, current_user)
    else:
        raise HTTPException(status_code=400, detail="action must be approve|reject")
    db.commit()
    db.refresh(e)
    db.refresh(training)
    return {"id": str(e.id), "status": e.status, "reason": reason}

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


def publish_training_service(db: Session, tid: UUID, current_user: dict | None = None):
    return update_training_status_service(db, tid, "published")


def unpublish_training_service(db: Session, tid: UUID, current_user: dict | None = None):
    return update_training_status_service(db, tid, "unpublished")


def suspend_training_service(db: Session, tid: UUID, reason: str | None = None, current_user: dict | None = None):
    training = _get_training_or_404(db, tid)
    result = update_training_status_service(db, tid, "suspended")
    if reason:
        _append_moderation(db, training, "suspended", reason, current_user)
        db.commit()
    return result


def cancel_training_service(db: Session, tid: UUID, reason: str | None = None, current_user: dict | None = None):
    training = _get_training_or_404(db, tid)
    result = update_training_status_service(db, tid, "cancelled")
    if reason:
        _append_moderation(db, training, "cancelled", reason, current_user)
        db.commit()
    return result


def delete_section_service(db: Session, tid: UUID, section_id: str):
    from sqlalchemy.orm.attributes import flag_modified
    training = _get_training_or_404(db, tid)
    original = len(training.sections or [])
    training.sections = [s for s in (training.sections or []) if s.get("id") != section_id]
    if len(training.sections) == original:
        raise HTTPException(status_code=404, detail="Section not found")
    flag_modified(training, "sections")
    db.commit()
    return {"message": "Section deleted"}


def get_lesson_service(db: Session, tid: UUID, section_id: str, lesson_id: str, current_user: dict | None = None):
    training = _get_training_or_404(db, tid)
    section, lesson = _find_lesson(training, section_id, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    role = (current_user or {}).get("role")
    if lesson.get("is_draft") and role not in ("admin", "provider") and not lesson.get("is_preview"):
        raise HTTPException(status_code=403, detail="Draft lesson — not available to learners")
    return {"section_id": section_id, "section_title": (section or {}).get("title"), **lesson}


def list_lesson_topics_service(db: Session, tid: UUID, section_id: str, lesson_id: str):
    training = _get_training_or_404(db, tid)
    _, lesson = _find_lesson(training, section_id, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    return lesson.get("topics") or []


def add_lesson_topic_service(db: Session, tid: UUID, section_id: str, lesson_id: str, payload: dict):
    import uuid as _uuid
    from sqlalchemy.orm.attributes import flag_modified
    training = _get_training_or_404(db, tid)
    _, lesson = _find_lesson(training, section_id, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    topics = list(lesson.get("topics") or [])
    new_topic = {"id": str(_uuid.uuid4()), **payload}
    topics.append(new_topic)
    lesson["topics"] = topics
    flag_modified(training, "sections")
    db.commit()
    return new_topic


def update_lesson_topic_service(db: Session, tid: UUID, section_id: str, lesson_id: str, topic_id: str, payload: dict):
    from sqlalchemy.orm.attributes import flag_modified
    training = _get_training_or_404(db, tid)
    _, lesson = _find_lesson(training, section_id, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    for topic in lesson.get("topics") or []:
        if topic.get("id") == topic_id:
            topic.update({k: v for k, v in payload.items() if k != "id"})
            flag_modified(training, "sections")
            db.commit()
            return topic
    raise HTTPException(status_code=404, detail="Topic not found")


def delete_lesson_topic_service(db: Session, tid: UUID, section_id: str, lesson_id: str, topic_id: str):
    from sqlalchemy.orm.attributes import flag_modified
    training = _get_training_or_404(db, tid)
    _, lesson = _find_lesson(training, section_id, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    topics = [t for t in (lesson.get("topics") or []) if t.get("id") != topic_id]
    if len(topics) == len(lesson.get("topics") or []):
        raise HTTPException(status_code=404, detail="Topic not found")
    lesson["topics"] = topics
    flag_modified(training, "sections")
    db.commit()
    return {"message": "Topic deleted"}


def update_assessment_service(db: Session, tid: UUID, aid: str, payload: dict):
    import copy
    from sqlalchemy.orm.attributes import flag_modified
    training = _get_training_or_404(db, tid)
    assessments = copy.deepcopy(training.assessments or [])
    for assessment in assessments:
        if str(assessment.get("id")) == str(aid):
            assessment.update({k: v for k, v in payload.items() if k not in ("id", "questions")})
            training.assessments = assessments
            flag_modified(training, "assessments")
            db.commit()
            return assessment
    raise HTTPException(status_code=404, detail="Assessment not found")


def delete_assessment_service(db: Session, tid: UUID, aid: str):
    from sqlalchemy.orm.attributes import flag_modified
    training = _get_training_or_404(db, tid)
    original = len(training.assessments or [])
    training.assessments = [a for a in (training.assessments or []) if str(a.get("id")) != str(aid)]
    if len(training.assessments) == original:
        raise HTTPException(status_code=404, detail="Assessment not found")
    flag_modified(training, "assessments")
    db.commit()
    return {"message": "Assessment deleted"}


def delete_assessment_question_service(db: Session, tid: UUID, aid: str, qid: str):
    import copy
    from sqlalchemy.orm.attributes import flag_modified
    training = _get_training_or_404(db, tid)
    assessments = copy.deepcopy(training.assessments or [])
    for assessment in assessments:
        if str(assessment.get("id")) != str(aid):
            continue
        questions = [q for q in assessment.get("questions", []) if str(q.get("id")) != str(qid)]
        if len(questions) == len(assessment.get("questions", [])):
            raise HTTPException(status_code=404, detail="Question not found")
        assessment["questions"] = questions
        training.assessments = assessments
        flag_modified(training, "assessments")
        db.commit()
        return {"message": "Question deleted"}
    raise HTTPException(status_code=404, detail="Assessment not found")


def filter_assessments(assessments: list, module_id: str | None, lesson_id: str | None) -> list:
    if not module_id and not lesson_id:
        return assessments
    filtered = []
    for assessment in assessments:
        if module_id:
            assessment_module = str(assessment.get("module_id") or assessment.get("section_id") or "")
            if assessment_module and assessment_module != str(module_id):
                continue
        if lesson_id:
            assessment_lesson = str(assessment.get("lesson_id") or "")
            if assessment_lesson and assessment_lesson != str(lesson_id):
                continue
        filtered.append(assessment)
    return filtered


def get_secure_training_content_service(db: Session, tid: UUID, current_user: dict):
    from app.models.training_model import TrainingEnrolment
    import copy
    email = current_user.get("email")
    enrol = db.query(TrainingEnrolment).filter(
        TrainingEnrolment.training_id == tid,
        TrainingEnrolment.participant_email == email,
        TrainingEnrolment.status.in_(["enrolled", "active", "completed"]),
    ).first() if email else None
    if not enrol and current_user.get("role") not in ["admin", "provider"]:
        raise HTTPException(status_code=403, detail="Enrolled participants only")
    training = _get_training_or_404(db, tid)
    if training.status in ("draft", "cancelled", "archived") and current_user.get("role") not in ["admin", "provider"]:
        raise HTTPException(status_code=403, detail=f"Content not available — training is {training.status}")
    sections = copy.deepcopy(training.sections or [])
    assessments = copy.deepcopy(training.assessments or [])
    role = current_user.get("role")
    if role not in ["admin", "provider"]:
        filtered_sections = []
        completed = set()
        if email:
            from app.models.training_model import TrainingProgress
            prog = db.query(TrainingProgress).filter(
                TrainingProgress.training_id == tid,
                TrainingProgress.participant_email == email,
            ).first()
            if prog:
                completed = set(prog.lessons_completed or [])
        for section in sections:
            section_copy = {**section, "lessons": []}
            for lesson in section.get("lessons", []):
                if lesson.get("is_draft") and not lesson.get("is_preview"):
                    continue
                accessible, _ = _lesson_is_accessible(lesson, completed, enrol, training)
                if accessible or lesson.get("is_preview"):
                    section_copy["lessons"].append(lesson)
            if section_copy["lessons"]:
                filtered_sections.append(section_copy)
        sections = filtered_sections
        for assessment in assessments:
            for question in assessment.get("questions", []):
                question.pop("correct_answer", None)
                question.pop("explanation", None)
    return {"training_id": str(tid), "sections": sections, "assessments": assessments}


def reply_discussion_service(db: Session, tid: UUID, discussion_id: str, payload: dict, current_user: dict):
    from sqlalchemy.orm.attributes import flag_modified
    from datetime import datetime
    training = _get_training_or_404(db, tid)
    discussions = list(getattr(training, "discussions", None) or [])
    for entry in discussions:
        if entry.get("id") != discussion_id:
            continue
        replies = list(entry.get("replies") or [])
        reply = {
            "id": str(__import__("uuid").uuid4()),
            "author": current_user.get("email", "anonymous"),
            "text": payload.get("text") or payload.get("answer") or "",
            "created_at": datetime.utcnow().isoformat(),
            "is_answer": bool(payload.get("is_answer")),
        }
        if reply["is_answer"] or current_user.get("role") in ("admin", "provider"):
            entry["answer"] = reply["text"]
            entry["answered_by"] = reply["author"]
            entry["answered_at"] = reply["created_at"]
        replies.append(reply)
        entry["replies"] = replies
        training.discussions = discussions
        flag_modified(training, "discussions")
        db.commit()
        return entry
    raise HTTPException(status_code=404, detail="Discussion not found")


def get_moderation_history_service(db: Session, tid: UUID):
    training = _get_training_or_404(db, tid)
    return list(getattr(training, "moderation_history", None) or [])
