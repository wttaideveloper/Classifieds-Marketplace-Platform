from uuid import UUID
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.models.enterprise_model import Enterprise
from app.models.location_model import EnterpriseLocation
from app.repository.training_repo import create_training, delete_training, get_training_by_id, get_trainings, update_training
from app.repository.query_utils import build_pagination_meta
from app.schemas.training_schema import TrainingDetailResponse, TrainingListItemResponse, TrainingPaginatedResponse, TrainingResponse
from app.services.response_mappers import _qr_image_base64, map_training_detail, map_training_list_item, map_training_write

ACTIVE_ENROLMENT_STATUSES = frozenset({"enrolled", "active", "completed", "approved"})
CHECKIN_ELIGIBLE_STATUSES = frozenset({"enrolled", "active", "approved"})
# Statuses that occupy a capacity slot — used both when enrolling and when
# promoting from the waitlist so the two stay in agreement.
ENROLMENT_CAPACITY_STATUSES = frozenset({"enrolled", "pending_approval", "active", "attended"})


def _is_active_enrolment(enrolment) -> bool:
    return bool(enrolment and enrolment.status in ACTIVE_ENROLMENT_STATUSES)


def _generate_pass_code() -> str:
    import secrets
    return f"{secrets.randbelow(1_000_000):06d}"


def _build_qr_payload(training_id, pass_code: str) -> str:
    import json
    return json.dumps({"training_id": str(training_id), "pass_code": pass_code})


# Contract delivery_mode vocabulary (online|physical|hybrid|self_paced). The
# older self_paced|instructor_led|blended values already stored on existing
# trainings are left ungated for backward compatibility — only these four
# values trigger the field-gating rules below.
DELIVERY_MODE_ACCESS_TYPE = {
    "online": "online",
    "physical": "venue",
    "hybrid": "both",
    "self_paced": "on_demand",
    "recorded": "on_demand",
}


def _access_type_for_delivery_mode(delivery_mode: str | None) -> str | None:
    return DELIVERY_MODE_ACCESS_TYPE.get(delivery_mode)


def _validate_delivery_mode_fields(delivery_mode: str | None, venue, meeting_link, sections=None) -> None:
    if not meeting_link:
        meeting_link = next((item.get("meeting_link") or item.get("join_url")
                             for section in sections or []
                             for item in section.get("items", section.get("lessons", [])) or []
                             if item.get("type") == "live" and (item.get("meeting_link") or item.get("join_url"))), None)
    if delivery_mode == "online":
        if not meeting_link:
            raise HTTPException(status_code=400, detail="meeting_link is required when delivery_mode is 'online'")
    elif delivery_mode == "physical":
        if not venue:
            raise HTTPException(status_code=400, detail="venue is required when delivery_mode is 'physical'")
    elif delivery_mode == "hybrid":
        if not venue:
            raise HTTPException(status_code=400, detail="venue is required when delivery_mode is 'hybrid'")
        if not meeting_link:
            raise HTTPException(status_code=400, detail="meeting_link is required when delivery_mode is 'hybrid'")
    # self_paced and any legacy/unrecognized value: no gating.

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
    from app.services.training_form_config_service import apply_form_configuration_to_training_data

    form_meta = apply_form_configuration_to_training_data(db, data, current_user or {})
    _validate(db, data.enterprise_id, data.location_id, current_user)
    payload = data.to_model_data()
    payload.update(form_meta)
    from app.services.training_curriculum import normalize_authoring
    payload = normalize_authoring(payload)
    create_status = payload.get("status") or "draft"
    if create_status not in ("draft", "pending_approval"):
        create_status = "draft"
    payload["status"] = create_status
    if not payload.get("tenant_id"):
        ent = db.query(Enterprise).filter(Enterprise.id == payload["enterprise_id"]).first()
        if ent and ent.tenant_id:
            payload["tenant_id"] = ent.tenant_id
    for key in ("sections", "assessments", "assignments", "discussions", "announcements", "tags", "gallery_images", "documents", "moderation_history"):
        if payload.get(key) is None:
            payload[key] = []
    if payload.get("custom_values") is None:
        payload["custom_values"] = []
    _validate_delivery_mode_fields(payload.get("delivery_mode"), payload.get("venue"), payload.get("meeting_link"), payload.get("sections"))
    if payload.get("check_in"):
        import uuid as _uuid
        training_id = payload.get("id") or _uuid.uuid4()
        payload["id"] = training_id
        payload["pass_code"] = _generate_pass_code()
        payload["qr_payload"] = _build_qr_payload(training_id, payload["pass_code"])
    from app.models.training_model import Training
    obj = Training(**payload)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return TrainingResponse.model_validate(map_training_write(obj))

def get_trainings_service(db: Session, **kw):
    items, total = get_trainings(db, **kw)

    from app.models.training_model import TrainingReview
    rating_rows = (
        db.query(TrainingReview.training_id, TrainingReview.rating)
        .filter(TrainingReview.training_id.in_([i.id for i in items]))
        .all()
        if items else []
    )
    ratings_by_training: dict = {}
    for training_id, rating in rating_rows:
        ratings_by_training.setdefault(training_id, []).append(int(rating))

    mapped = []
    for i in items:
        d = map_training_list_item(i)
        ratings = ratings_by_training.get(i.id, [])
        d["average_rating"] = round(sum(ratings) / len(ratings), 2) if ratings else 0
        d["reviews_count"] = len(ratings)
        mapped.append(TrainingListItemResponse.model_validate(d))

    return TrainingPaginatedResponse(items=mapped, pagination=build_pagination_meta(total, kw.get("page",1), kw.get("page_size",20)))

def _sanitize_assessment_for_learner(assessment: dict) -> dict:
    """Deep copy of an assessment with correct_answer and reusable (an
    authoring/question-bank flag) stripped from every question — never
    expose grading keys to a learner-facing response."""
    import copy

    sanitized = copy.deepcopy(assessment)
    for q in sanitized.get("questions") or []:
        if isinstance(q, dict):
            q.pop("correct_answer", None)
            q.pop("reusable", None)
    return sanitized


def _embed_assessments_into_sections(sections: list | None, assessments: list | None) -> list:
    """Embed the matching sanitized assessment object (by assessment_id)
    directly onto each section/lesson that references one, alongside the
    existing assessment_id — so mobile can render a quiz without a second
    round trip. Operates on a deep copy; never mutates the ORM-tracked
    sections/assessments JSONB columns."""
    import copy

    by_id = {a.get("id"): a for a in (assessments or []) if isinstance(a, dict) and a.get("id")}
    out = copy.deepcopy(sections or [])
    for section in out:
        if not isinstance(section, dict):
            continue
        sec_aid = section.get("assessment_id")
        section["assessment"] = _sanitize_assessment_for_learner(by_id[sec_aid]) if sec_aid in by_id else None
        for lesson in section.get("lessons") or []:
            if not isinstance(lesson, dict):
                continue
            les_aid = lesson.get("assessment_id")
            lesson["assessment"] = _sanitize_assessment_for_learner(by_id[les_aid]) if les_aid in by_id else None
        if "items" in section:
            section["items"] = copy.deepcopy(section.get("lessons") or [])
    return out


def get_training_service(db: Session, tid: UUID, current_user: dict | None = None):
    obj = get_training_by_id(db, tid)
    if not obj: raise HTTPException(status_code=404, detail="Training not found")
    detail = map_training_detail(obj)
    if not current_user or current_user.get("role") not in ("admin", "provider", "super_admin"):
        detail["sections"] = _embed_assessments_into_sections(detail.get("sections"), detail.get("assessments"))
    from app.models.training_model import TrainingEnrolment
    enrolled_count = db.query(TrainingEnrolment).filter(
        TrainingEnrolment.training_id == tid,
        TrainingEnrolment.status.in_(ACTIVE_ENROLMENT_STATUSES),
    ).count()
    detail["enrolled_count"] = enrolled_count
    detail["current_participants"] = enrolled_count
    available_slots = None
    try:
        if obj.capacity is not None and str(obj.capacity).strip():
            available_slots = max(0, int(str(obj.capacity)) - enrolled_count)
    except (TypeError, ValueError):
        available_slots = None
    detail["available_slots"] = available_slots

    from app.models.training_model import TrainingReview, TrainingWaitlist
    all_reviews = db.query(TrainingReview).filter(TrainingReview.training_id == tid).all()
    ratings = [int(r.rating) for r in all_reviews]
    detail["average_rating"] = round(sum(ratings) / len(ratings), 2) if ratings else 0
    detail["reviews_count"] = len(ratings)
    recent_reviews = sorted(all_reviews, key=lambda r: r.created_at, reverse=True)[:50]
    detail["reviews"] = [
        {
            "id": r.id,
            "training_id": r.training_id,
            "participant_email": r.participant_email,
            "rating": r.rating,
            "comment": r.comment,
            "created_at": r.created_at,
        }
        for r in recent_reviews
    ]
    detail["waitlist_count"] = db.query(TrainingWaitlist).filter(TrainingWaitlist.training_id == tid).count()

    return TrainingDetailResponse.model_validate(detail)

def update_training_service(db: Session, tid: UUID, data, current_user: dict | None = None):
    from app.services.training_form_config_service import apply_form_configuration_to_training_update

    obj = get_training_by_id(db, tid, include_deleted=True)
    if not obj or obj.is_deleted: raise HTTPException(status_code=404, detail="Training not found")
    lid = data.location_id if getattr(data,"location_id",None) is not None else obj.location_id
    _validate(db, obj.enterprise_id, lid)
    final_delivery_mode = data.delivery_mode if getattr(data, "delivery_mode", None) is not None else obj.delivery_mode
    final_venue = data.venue if getattr(data, "venue", None) is not None else obj.venue
    final_meeting_link = data.meeting_link if getattr(data, "meeting_link", None) is not None else obj.meeting_link
    _validate_delivery_mode_fields(final_delivery_mode, final_venue, final_meeting_link, getattr(data, "sections", None) if getattr(data, "sections", None) is not None else obj.sections)
    extra = apply_form_configuration_to_training_update(db, obj, data, current_user or {})
    updated = update_training(db, obj, data)
    if updated.check_in and not updated.pass_code:
        updated.pass_code = _generate_pass_code()
        updated.qr_payload = _build_qr_payload(updated.id, updated.pass_code)
        db.commit()
        db.refresh(updated)
    if extra:
        for key, val in extra.items():
            setattr(updated, key, val)
        db.commit()
        db.refresh(updated)
    return TrainingResponse.model_validate(map_training_write(updated))

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

def update_training_status_service(db: Session, tid: UUID, st: str, current_user: dict | None = None, notes: str | None = None):
    obj = get_training_by_id(db, tid, include_deleted=True)
    if not obj:
        raise HTTPException(status_code=404, detail="Training not found")
    # Soft-deleted archived rows can still be restored to draft
    if obj.is_deleted and not (obj.status == "archived" and st == "draft"):
        raise HTTPException(status_code=404, detail="Training not found")
    if current_user and current_user.get("role") not in ("admin", "super_admin"):
        user_tid = current_user.get("tenant_id")
        if user_tid and obj.tenant_id and str(obj.tenant_id) != str(user_tid):
            raise HTTPException(status_code=403, detail="Not authorized for this tenant")
    VALID = {
        "pending_approval": ["approved", "cancelled", "rejected", "needs_revision"],
        "approved": ["draft", "published", "unpublished", "cancelled", "archived"],
        "draft": ["pending_approval", "cancelled", "archived"],
        "published": ["completed", "cancelled", "suspended", "unpublished", "archived"],
        "unpublished": ["published", "draft", "cancelled", "archived"],
        "suspended": ["published", "unpublished", "cancelled", "archived"],
        "completed": ["archived"],
        "cancelled": ["draft", "archived"],
        "rejected": ["draft", "pending_approval", "archived"],
        "needs_revision": ["pending_approval", "draft", "cancelled"],
        "archived": ["draft"],
    }
    allowed = VALID.get(obj.status, [])
    if st not in allowed:
        raise HTTPException(status_code=400, detail=f"Cannot transition from '{obj.status}' to '{st}'. Allowed: {allowed}")
    from datetime import datetime

    previous_status = obj.status
    obj.status = st
    if st == "draft":
        obj.is_deleted = False
        obj.moderation_status = "draft"
    if st in ("rejected", "needs_revision") and notes:
        obj.last_admin_notes = notes
        obj.rejection_reason = notes
    _MODERATION_STATUS_BY_TRANSITION = {
        "pending_approval": "pending",
        "approved": "approved",
        "rejected": "rejected",
        "needs_revision": "changes_requested",
    }
    if st in _MODERATION_STATUS_BY_TRANSITION:
        obj.moderation_status = _MODERATION_STATUS_BY_TRANSITION[st]
    now = datetime.utcnow()
    if st == "approved":
        obj.approved_at = now
    elif st == "published":
        obj.published_at = now
        obj.moderation_status = "approved"
        if obj.delivery_mode in ("physical", "hybrid") and not obj.pass_code:
            obj.check_in = True
            obj.pass_code = _generate_pass_code()
            obj.qr_payload = _build_qr_payload(obj.id, obj.pass_code)
    elif st == "archived":
        obj.archived_at = now
    elif st == "suspended":
        obj.suspended_at = now
    elif st == "cancelled":
        obj.cancelled_at = now
    _append_moderation(db, obj, st, notes, current_user, previous_status=previous_status, new_status=st)
    db.commit(); db.refresh(obj); return TrainingResponse.model_validate(map_training_write(obj))


def restore_training_service(db: Session, tid: UUID, current_user: dict | None = None):
    """Restore an archived training back to draft (clears is_deleted)."""
    return update_training_status_service(db, tid, "draft", current_user)

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


def lesson_progress_percent(completed: int, total: int) -> float:
    """Single source of truth for the raw learner-facing lesson-completion
    percentage — used by /my/enrolments, /content, and /progress alike so all
    three always agree given the same completed/total counts. Deliberately NOT
    filtered to mandatory-only lessons; that is a separate business rule (see
    complete_lesson_service's completed_at/certificate_url logic) exposed under
    its own mandatory_done/mandatory_total (and completed_required_items/
    total_required_items in /content) fields instead."""
    return round(completed / total * 100, 2) if total else 0


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
        TrainingEnrolment.status.in_(ENROLMENT_CAPACITY_STATUSES),
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
    import uuid as _uuid
    from datetime import datetime as _dt, timedelta
    status = "pending_approval" if getattr(training, "requires_approval", False) else "enrolled"
    expires = None
    if getattr(training, "access_duration_days", None):
        try:
            expires = _dt.utcnow() + timedelta(days=int(training.access_duration_days))
        except Exception:
            pass
    promoted = TrainingEnrolment(
        training_id=tid,
        participant_name=next_wait.participant_name,
        participant_email=next_wait.participant_email,
        status=status,
        qr_code=str(_uuid.uuid4())[:12].upper(),
        access_expires_at=expires,
    )
    db.add(promoted)
    db.delete(next_wait)
    db.commit()
    db.refresh(promoted)
    return {"enrolment_id": str(promoted.id), "participant_email": promoted.participant_email, "status": promoted.status}


def join_waitlist_service(db: Session, tid: UUID, payload: dict | None = None, current_user: dict | None = None):
    from app.models.training_model import TrainingEnrolment, TrainingWaitlist
    _get_training_or_404(db, tid)
    participant_email = (payload or {}).get("participant_email") or (current_user or {}).get("email")
    if not participant_email:
        raise HTTPException(status_code=400, detail="participant_email is required")
    participant_name = (payload or {}).get("participant_name") or (current_user or {}).get("name") or participant_email
    existing_enrol = db.query(TrainingEnrolment).filter(
        TrainingEnrolment.training_id == tid,
        TrainingEnrolment.participant_email == participant_email,
    ).first()
    if existing_enrol and existing_enrol.status not in ("cancelled", "expired"):
        raise HTTPException(status_code=400, detail="Already enrolled on this training")
    existing_wait = db.query(TrainingWaitlist).filter(
        TrainingWaitlist.training_id == tid,
        TrainingWaitlist.participant_email == participant_email,
    ).first()
    if existing_wait:
        raise HTTPException(status_code=400, detail="Already on the waitlist for this training")
    w = TrainingWaitlist(training_id=tid, participant_name=participant_name, participant_email=participant_email)
    db.add(w)
    db.commit()
    db.refresh(w)
    position = db.query(TrainingWaitlist).filter(TrainingWaitlist.training_id == tid).count()
    return {
        "id": str(w.id),
        "training_id": str(tid),
        "participant_name": participant_name,
        "participant_email": participant_email,
        "status": "waitlisted",
        "position": position,
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }


def leave_waitlist_service(db: Session, tid: UUID, entry_id: UUID, participant_email: str | None = None):
    from app.models.training_model import TrainingWaitlist
    q = db.query(TrainingWaitlist).filter(TrainingWaitlist.id == entry_id, TrainingWaitlist.training_id == tid)
    if participant_email:
        q = q.filter(TrainingWaitlist.participant_email == participant_email)
    w = q.first()
    if not w:
        raise HTTPException(status_code=404, detail="Waitlist entry not found")
    db.delete(w)
    db.commit()
    promoted = _promote_waitlist(db, tid)
    result = {"message": "Removed from waitlist"}
    if promoted:
        result["waitlist_promoted"] = promoted
    return result


def _append_moderation(db: Session, training, action: str, reason: str | None, actor: dict | None, *, previous_status=None, new_status=None):
    from datetime import datetime
    history = list(getattr(training, "moderation_history", None) or [])
    history.append({
        "action": action,
        **({"previous_status": previous_status, "new_status": new_status} if new_status is not None else {}),
        "actor_id": str((actor or {}).get("id")) if (actor or {}).get("id") is not None else None,
        "reason": reason,
        "actor_email": (actor or {}).get("email"),
        "actor_role": (actor or {}).get("role"),
        "at": datetime.utcnow().isoformat(),
    })
    training.moderation_history = history

def add_assessment_question_service(db: Session, tid: UUID, aid: str, data):
    import uuid as _uuid, copy
    from sqlalchemy.orm.attributes import flag_modified
    t = _get_training_or_404(db, tid)
    assessments = copy.deepcopy(t.assessments or [])
    for a in assessments:
        if str(a.get("id")) == str(aid):
            qs = a.get("questions")
            if qs is None:
                qs = []
            if not isinstance(qs, list):
                raise HTTPException(status_code=400, detail="Assessment questions must be an array or null")
            new_q = {"id": str(_uuid.uuid4()), **data.model_dump(mode="json")}
            qs.append(new_q)
            a["questions"] = qs
            t.assessments = assessments
            flag_modified(t, "assessments")
            db.commit(); db.refresh(t)
            return next(q for assessment in t.assessments if str(assessment.get("id")) == str(aid)
                        for q in assessment["questions"] if q["id"] == new_q["id"])
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
    attempts_allowed_declared = target.get("attempt_limit") or target.get("attempts_allowed")
    attempt_limit = int(attempts_allowed_declared or 999)
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
    pending_points = 0
    for ans in answers:
        qid = str(ans.get("question_id") or ans.get("id") or "")
        given = str(ans.get("answer", "")).strip().lower()
        q = qmap.get(qid)
        if not q:
            continue
        qtype = q.get("question_type") or "mcq"
        
        if qtype in ["short_answer", "essay", "blank_text"]:
            correct = str(q.get("correct_answer", "")).strip().lower()
            if qtype in ["short_answer", "blank_text"] and correct:
                pass # Can be auto-graded, fall through to else block
            else:
                needs_manual = True
                pending_points += int(q.get("points", 1))
                continue
            
        options = q.get("options") or []
        id_to_label = {}
        for opt in options:
            if isinstance(opt, dict):
                opt_id = str(opt.get("id", "")).strip().lower()
                opt_label = str(opt.get("label", opt.get("value", ""))).strip().lower()
                if opt_id: id_to_label[opt_id] = opt_label
            else:
                opt_str = str(opt).strip().lower()
                id_to_label[opt_str] = opt_str

        if qtype == "multiple_select":
            correct = str(q.get("correct_answer", "")).strip().lower()
            given_parts = [s.strip() for s in given.split(",") if s.strip()]
            correct_parts = [s.strip() for s in correct.split(",") if s.strip()]
            
            given_mapped = set([id_to_label.get(p, p) for p in given_parts])
            correct_mapped = set([id_to_label.get(p, p) for p in correct_parts])
            
            if given_mapped == correct_mapped and given_mapped:
                score += int(q.get("points", 1))
        elif qtype == "true_false":
            correct = str(q.get("correct_answer", "")).strip().lower()
            if correct in ["yes", "y", "1"]: correct = "true"
            elif correct in ["no", "n", "0"]: correct = "false"
            
            given_mapped = str(given).strip().lower()
            if given_mapped in ["yes", "y", "1"]: given_mapped = "true"
            elif given_mapped in ["no", "n", "0"]: given_mapped = "false"
            
            if given_mapped and correct and given_mapped == correct:
                score += int(q.get("points", 1))
        else:
            correct = str(q.get("correct_answer", "")).strip().lower()
            given_mapped = id_to_label.get(given, given)
            correct_mapped = id_to_label.get(correct, correct)
            
            if given and correct and given_mapped == correct_mapped:
                score += int(q.get("points", 1))
                
    import math
    passing = (math.ceil(total * float(target["pass_percent"]) / 100)
               if target.get("pass_percent") is not None else
               int(target.get("passing_score") or target.get("pass_mark") or (total * 0.6 if total else 0)))
               
    can_pass = (score + pending_points) >= passing
    passed = score >= passing and not needs_manual
    
    if passed:
        feedback = "Passed"
    elif can_pass and needs_manual:
        feedback = "Pending manual evaluation"
    else:
        feedback = "Failed"
        passed = False
        
    sub = TrainingAssessmentSubmission(training_id=tid, assessment_id=str(aid), participant_email=participant_email, answers=answers, score=str(score), passed=passed)
    db.add(sub); db.commit(); db.refresh(sub)
    # Mirrors get_secure_training_content_service's per-lesson is_completed rule for
    # exam/quiz lessons (submission is not None, regardless of pass/fail) — any
    # submission now also reaches TrainingProgress.lessons_completed, the single
    # source of truth my/enrolments and /progress read from.
    _mark_lessons_completed_for_reference(db, tid, participant_email, key="assessment_id", ref_id=aid, t=t)
    publication = target.get("publication") or target.get("result_publication") or "immediate"
    return {"score": score, "passed": passed, "total_points": total, "feedback": feedback, "assessment_id": str(aid), "submission_id": str(sub.id), "publication": publication, "needs_manual": needs_manual, "attempts_made": cnt + 1, "attempts_allowed": int(attempts_allowed_declared) if attempts_allowed_declared else None}

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


def list_training_assignments_service(db: Session, tid: UUID, current_user: dict | None = None) -> list[dict]:
    t = _get_training_or_404(db, tid)
    items = []
    email = current_user.get("email") if current_user else None
    
    from app.models.training_model import TrainingAssignmentSubmission, TrainingAssessmentSubmission
    
    assignments = list(t.assignments or [])
    for a in assignments:
        item = dict(a)
        item["type"] = "assignment"
        if email:
            item["attempts_made"] = db.query(TrainingAssignmentSubmission).filter(
                TrainingAssignmentSubmission.training_id == tid,
                TrainingAssignmentSubmission.assignment_id == str(item.get("id")),
                TrainingAssignmentSubmission.participant_email == email
            ).count()
        else:
            item["attempts_made"] = 0
        items.append(item)
        
    assessments = list(t.assessments or [])
    for a in assessments:
        item = dict(a)
        item["type"] = "assessment"
        if email:
            item["attempts_made"] = db.query(TrainingAssessmentSubmission).filter(
                TrainingAssessmentSubmission.training_id == tid,
                TrainingAssessmentSubmission.assessment_id == str(item.get("id")),
                TrainingAssessmentSubmission.participant_email == email
            ).count()
        else:
            item["attempts_made"] = 0
        items.append(item)
        
    return items


def delete_training_assignment_service(db: Session, tid: UUID, aid: str) -> dict:
    import copy
    from sqlalchemy.orm.attributes import flag_modified
    t = _get_training_or_404(db, tid)
    original = list(t.assignments or [])
    remaining = [a for a in original if str(a.get("id")) != str(aid)]
    if len(remaining) == len(original):
        raise HTTPException(status_code=404, detail="Assignment not found")
    t.assignments = remaining
    flag_modified(t, "assignments")
    db.commit()
    return {"message": "Assignment deleted", "assignment_id": str(aid)}


def get_training_admin_notes_service(db: Session, tid: UUID) -> dict:
    t = _get_training_or_404(db, tid)
    return {
        "training_id": str(tid),
        "status": t.status,
        "last_admin_notes": getattr(t, "last_admin_notes", None),
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }

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
    normalized = {str(x).lower() if str(x).startswith(".") else f".{str(x).lower()}" for x in accepted}
    import os
    is_model = hasattr(payload, "model_dump")
    file_url = payload.file_url if is_model else payload.get("file_url") if isinstance(payload, dict) else None
    files = [f.model_dump() if hasattr(f, "model_dump") else dict(f)
             for f in (payload.files if is_model else payload.get("files") if isinstance(payload, dict) else []) or []]
    def _validate_files(files_to_check, label):
        for f in files_to_check:
            ftype = str(f.get("name") or f.get("url") or "")
            ext = os.path.splitext(ftype)[-1].lower()
            if ext and normalized and ext not in normalized:
                raise HTTPException(status_code=400, detail=f"File type {ext} not allowed. Accepted: {sorted(normalized)}")
    _validate_files(files, "multi-file")
    if accepted and file_url:
        ext = os.path.splitext(str(file_url))[-1].lower()
        if ext and normalized and ext not in normalized:
            raise HTTPException(status_code=400, detail=f"File type {ext} not allowed. Accepted: {sorted(normalized)}")
    if not file_url and files:
        file_url = files[0].get("url")
    text = payload.submission_text if is_model else payload.get("submission_text") if isinstance(payload, dict) else None
    sub = TrainingAssignmentSubmission(training_id=tid, assignment_id=str(aid), participant_email=participant_email, file_url=file_url, files=files, submission_text=text)
    db.add(sub); db.commit(); db.refresh(sub)
    # Mirrors get_secure_training_content_service's existing in-memory
    # assignment_submissions merge — any submission (graded or not) now also
    # reaches TrainingProgress.lessons_completed, not just the in-request view.
    _mark_lessons_completed_for_reference(db, tid, participant_email, key="assignment_id", ref_id=aid, t=t)
    attempts_made = db.query(TrainingAssignmentSubmission).filter(TrainingAssignmentSubmission.training_id==tid, TrainingAssignmentSubmission.assignment_id==str(aid), TrainingAssignmentSubmission.participant_email==participant_email).count()
    return {"id": str(sub.id), "submitted_at": sub.submitted_at.isoformat(), "grade": None, "feedback": None, "assignment_id": str(aid), "files": [dict(f) for f in (sub.files or [])], "attempts_made": attempts_made}

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
    overall = lesson_progress_percent(lessons_done, total_lessons) if total_lessons else round((sections_done / total_sections * 100) if total_sections else 0, 2)
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
    return {"id": str(sub.id), "grade": sub.grade, "feedback": sub.feedback, "files": [dict(f) for f in (sub.files or [])], "resubmission_allowed": True}

def _apply_lesson_completion(db: Session, tid: UUID, lesson_id: str, participant_email: str, t=None) -> dict:
    """Marks lesson_id complete for participant_email and recomputes section
    completion / overall_percent / mandatory-completion / certificate eligibility.

    Shared by complete_lesson_service (learner clicks "mark complete" on a plain
    lesson) and by submit_assessment_service / submit_assignment_service (a
    quiz/exam or assignment lesson that is "completed" by submitting it, not by a
    separate complete-lesson call). Before this helper existed, quiz/assignment
    submissions were recorded only in their own submission tables and never
    reached TrainingProgress.lessons_completed, so a learner who finished every
    lesson via a mix of plain completions and quiz/assignment submissions was
    undercounted by every consumer of lessons_completed (my/enrolments, /content,
    /progress) even though /content's own per-lesson is_completed already
    recognized the quiz/assignment as done via its submission.
    """
    from datetime import datetime
    from app.models.training_model import TrainingProgress
    t = t or _get_training_or_404(db, tid)
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
    target_section_id = None
    for section in t.sections or []:
        if any(l.get("id") == lesson_id for l in section.get("lessons", [])):
            target_section_id = section.get("id")
            break
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
        prog.certificate_url=f"/api/v1/trainings/{tid}/certificate.pdf?participant_email={participant_email}"
    db.commit(); db.refresh(prog)
    return {"overall_percent": overall, "lessons_done": len(lessons), "total_lessons": total, "mandatory_done": mandatory_done, "mandatory_total": mandatory_total, "completed_at": prog.completed_at.isoformat() if prog.completed_at else None, "certificate_url": prog.certificate_url}


def _mark_lessons_completed_for_reference(db: Session, tid: UUID, participant_email: str, *, key: str, ref_id: str, t=None) -> None:
    """Finds every lesson referencing ref_id via `key` (assessment_id or
    assignment_id) and records completion for each — generic across trainings,
    never keyed by a specific training/lesson id."""
    t = t or _get_training_or_404(db, tid)
    for section in t.sections or []:
        for lesson in section.get("lessons", []):
            if lesson.get(key) is not None and str(lesson.get(key)) == str(ref_id) and lesson.get("id"):
                _apply_lesson_completion(db, tid, lesson["id"], participant_email, t=t)


def complete_lesson_service(db: Session, tid: UUID, lesson_id: str, participant_email: str):
    from app.models.training_model import TrainingProgress
    t = _get_training_or_404(db, tid)
    enrol = _get_enrolment(db, tid, participant_email)
    if not _is_active_enrolment(enrol):
        raise HTTPException(status_code=403, detail="Active enrolment required")
    prog = db.query(TrainingProgress).filter(
        TrainingProgress.training_id == tid,
        TrainingProgress.participant_email == participant_email,
    ).first()
    lessons_so_far = set(prog.lessons_completed or []) if prog else set()
    target_lesson = None
    for section in t.sections or []:
        for lesson in section.get("lessons", []):
            if lesson.get("id") == lesson_id:
                target_lesson = lesson
                break
        if target_lesson:
            break
    if not target_lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    accessible, reason = _lesson_is_accessible(target_lesson, lessons_so_far, enrol, t)
    if not accessible:
        raise HTTPException(status_code=403, detail=reason or "Lesson not accessible")
    result = _apply_lesson_completion(db, tid, lesson_id, participant_email, t=t)
    # last completed for resume
    return {"lesson_id": lesson_id, **result, "resume_lesson": lesson_id}

def _resolve_live_attendance_target(db: Session, tid: UUID, session_id: str):
    """Resolve standalone sessions and the curriculum IDs returned by /content."""
    from app.models.training_model import TrainingLiveSession
    from app.services.training_curriculum import normalize_curriculum
    try:
        session_uuid = UUID(str(session_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "Invalid session_id") from exc
    session = db.query(TrainingLiveSession).filter(
        TrainingLiveSession.training_id == tid, TrainingLiveSession.id == session_uuid,
    ).first()
    if session:
        return session, {"id": str(session.id), "title": session.title, "attendance": session.attendance}, False
    training = _get_training_or_404(db, tid)
    curriculum = normalize_curriculum(training.sections, training.assessments, training.assignments)
    for section in curriculum["sections"]:
        if str(section.get("id")) == str(session_uuid) and section.get("type") == "live":
            return training, section, True
        for item in section["lessons"]:
            if str(item["id"]) == str(session_uuid) and item["type"] == "live":
                return training, item, True
    raise HTTPException(404, "Live session not found")


def _live_attendance_rows(value):
    # The former duplicate POST handler stored an email-keyed object.
    if isinstance(value, dict):
        return [{"participant_email": email, "recorded_at": entry.get("recorded_at") or entry.get("joined_at")}
                for email, entry in value.items() if isinstance(entry, dict)]
    return list(value or [])


def _save_live_attendance(owner, session_id, attendance, embedded):
    from copy import deepcopy
    from sqlalchemy.orm.attributes import flag_modified
    if not embedded:
        owner.attendance = attendance
        flag_modified(owner, "attendance")
        return
    sections = deepcopy(owner.sections)
    for section in sections:
        if str(section.get("id")) == str(session_id):
            section["attendance"] = attendance
        # Preserve both aliases when an authoring payload stores both.
        for key in ("lessons", "items"):
            for item in section.get(key) or []:
                if str(item.get("id")) == str(session_id):
                    item["attendance"] = attendance
    owner.sections = sections
    flag_modified(owner, "sections")


def record_live_attendance_service(db: Session, tid: UUID, session_id: str, participant_email: str):
    from app.models.training_model import TrainingProgress
    from datetime import datetime

    owner, session, embedded = _resolve_live_attendance_target(db, tid, session_id)
    enrol = _get_enrolment(db, tid, participant_email)
    if not _is_active_enrolment(enrol):
        raise HTTPException(status_code=403, detail="Active enrolment required for attendance")

    attendance = _live_attendance_rows(session.get("attendance"))
    existing = next((a for a in attendance if a.get("participant_email") == participant_email), None)
    recorded_at = existing.get("recorded_at") if existing else datetime.utcnow().isoformat()
    if not existing:
        attendance.append({"participant_email": participant_email, "recorded_at": recorded_at})
        _save_live_attendance(owner, session["id"], attendance, embedded)

    # Curriculum progress uses the same lesson ID as /content.
    live_lesson_id = str(session["id"]) if embedded else f"live:{session['id']}"
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
    lessons = set(prog.lessons_completed or [])
    lessons.add(live_lesson_id)
    prog.lessons_completed = list(lessons)
    prog.last_accessed_at = datetime.utcnow()
    db.commit()

    return {
        "session_id": str(session_id),
        "participant_email": participant_email,
        "recorded_at": recorded_at,
        "progress_marked": True,
    }

def get_certificate_service(db: Session, tid: UUID, participant_email: str):
    from app.models.training_model import TrainingProgress
    prog=db.query(TrainingProgress).filter(TrainingProgress.training_id==tid, TrainingProgress.participant_email==participant_email).first()
    if not prog or not prog.certificate_url:
        raise HTTPException(status_code=404, detail="Certificate not yet available — complete mandatory lessons")
    return {"training_id": str(tid), "participant_email": participant_email, "certificate_url": prog.certificate_url, "completed_at": prog.completed_at.isoformat() if prog.completed_at else None, "overall_percent": prog.overall_percent}


def generate_certificate_pdf_service(db: Session, tid: UUID, participant_email: str) -> bytes:
    """Real Certificate of Completion PDF — 404s (via get_certificate_service)
    if the participant hasn't earned one yet, rather than serving a static
    placeholder URL."""
    from xml.sax.saxutils import escape

    training = _get_training_or_404(db, tid)
    cert = get_certificate_service(db, tid, participant_email)  # raises 404 if not earned

    enrol = _get_enrolment(db, tid, participant_email)
    participant_name = (enrol.participant_name if enrol else None) or participant_email

    from io import BytesIO

    from reportlab.lib.pagesizes import landscape, A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), topMargin=1 * inch, bottomMargin=1 * inch)
    styles = getSampleStyleSheet()
    centered = ParagraphStyle("Centered", parent=styles["Normal"], alignment=TA_CENTER)
    title_style = ParagraphStyle("CertTitle", parent=styles["Title"], alignment=TA_CENTER, fontSize=28)
    name_style = ParagraphStyle("CertName", parent=styles["Title"], alignment=TA_CENTER, fontSize=22, spaceBefore=20)

    completed_at = cert.get("completed_at") or ""
    story = [
        Paragraph("Certificate of Completion", title_style),
        Spacer(1, 0.4 * inch),
        Paragraph("This certifies that", centered),
        Paragraph(escape(str(participant_name)), name_style),
        Spacer(1, 0.2 * inch),
        Paragraph("has successfully completed", centered),
        Paragraph(escape(str(training.title or "the training")), ParagraphStyle("CertCourse", parent=styles["Heading2"], alignment=TA_CENTER)),
        Spacer(1, 0.3 * inch),
        Paragraph(f"Completed on {escape(str(completed_at)[:10])}" if completed_at else "", centered),
        Spacer(1, 0.2 * inch),
        Paragraph(f"Certificate ID: {tid}", ParagraphStyle("CertId", parent=styles["Normal"], alignment=TA_CENTER, fontSize=8, textColor=HexColor("#888888"))),
    ]
    doc.build(story)
    return buffer.getvalue()


def check_in_training_service(db: Session, tid: UUID, participant_email: str, pass_code: str):
    from datetime import datetime

    training = _get_training_or_404(db, tid)
    if not training.check_in:
        raise HTTPException(status_code=400, detail="Check-in is not enabled for this training")
    if not training.pass_code or pass_code != training.pass_code:
        raise HTTPException(status_code=403, detail="Invalid pass code")
    enrol = _get_enrolment(db, tid, participant_email)
    if not _is_active_enrolment(enrol):
        raise HTTPException(status_code=403, detail="Active enrolment required")
    enrol.checked_in_at = datetime.utcnow()
    db.commit()
    db.refresh(enrol)
    return {"training_id": str(tid), "participant_email": participant_email, "checked_in_at": enrol.checked_in_at.isoformat()}


# --- Per-enrolment QR check-in (admin/scanner-driven; mirrors the Event registration QR system) ---


def _find_enrolment_by_id_or_qr(db: Session, tid: UUID, enrolment_id, qr_code: str | None):
    from app.models.training_model import TrainingEnrolment

    if enrolment_id:
        return db.query(TrainingEnrolment).filter(
            TrainingEnrolment.id == enrolment_id,
            TrainingEnrolment.training_id == tid,
        ).first()
    if qr_code:
        return db.query(TrainingEnrolment).filter(
            TrainingEnrolment.qr_code == qr_code,
            TrainingEnrolment.training_id == tid,
        ).first()
    raise HTTPException(status_code=400, detail="enrolment_id or qr_code is required")


def validate_training_qr_service(db: Session, tid: UUID, qr_code: str | None):
    from datetime import datetime

    from app.models.training_model import TrainingEnrolment

    if not qr_code:
        raise HTTPException(status_code=400, detail="qr_code is required")

    training = _get_training_or_404(db, tid)
    enrol = db.query(TrainingEnrolment).filter(
        TrainingEnrolment.qr_code == qr_code,
        TrainingEnrolment.training_id == tid,
    ).first()
    if not enrol:
        raise HTTPException(status_code=404, detail="QR code not found for this training")
    if enrol.status == "cancelled":
        raise HTTPException(status_code=410, detail="QR code has been revoked — enrolment is cancelled")
    if enrol.access_expires_at and enrol.access_expires_at < datetime.utcnow():
        raise HTTPException(status_code=410, detail="QR code has expired — enrolment access has ended")

    return {
        "valid": True,
        "enrolment_id": enrol.id,
        "participant_name": enrol.participant_name,
        "participant_email": enrol.participant_email,
        "training_id": tid,
        "training_title": training.title,
        "status": enrol.status,
        "message": f"Valid enrolment: {enrol.participant_name} ({enrol.status})",
    }


def check_in_enrolment_service(db: Session, tid: UUID, enrolment_id, qr_code: str | None, current_user: dict | None = None):
    from datetime import datetime

    training = _get_training_or_404(db, tid)
    if training.status in ("cancelled", "archived"):
        raise HTTPException(status_code=400, detail=f"Cannot check-in: training is {training.status}")

    enrol = _find_enrolment_by_id_or_qr(db, tid, enrolment_id, qr_code)
    if not enrol:
        raise HTTPException(status_code=404, detail="Enrolment not found")
    if enrol.status == "cancelled":
        raise HTTPException(status_code=400, detail="Cannot check-in: enrolment is cancelled")
    if enrol.status == "attended":
        return {
            "message": "Already checked in",
            "enrolment_id": enrol.id,
            "participant_name": enrol.participant_name,
            "participant_email": enrol.participant_email,
            "status": enrol.status,
            "checked_in_at": enrol.checked_in_at.isoformat() if enrol.checked_in_at else None,
        }
    if enrol.status not in CHECKIN_ELIGIBLE_STATUSES:
        raise HTTPException(status_code=400, detail=f"Cannot check-in: enrolment status is '{enrol.status}'")

    enrol.status = "attended"
    enrol.checked_in_at = datetime.utcnow()
    enrol.checked_in_by = current_user.get("id") if current_user else None
    db.commit()
    db.refresh(enrol)

    return {
        "message": "Checked in successfully",
        "enrolment_id": enrol.id,
        "participant_name": enrol.participant_name,
        "participant_email": enrol.participant_email,
        "status": enrol.status,
        "checked_in_at": enrol.checked_in_at.isoformat() if enrol.checked_in_at else None,
    }


def uncheck_in_enrolment_service(db: Session, tid: UUID, enrolment_id, qr_code: str | None):
    enrol = _find_enrolment_by_id_or_qr(db, tid, enrolment_id, qr_code)
    if not enrol:
        raise HTTPException(status_code=404, detail="Enrolment not found")
    if enrol.status != "attended":
        raise HTTPException(status_code=400, detail=f"Cannot undo: enrolment status is '{enrol.status}', not 'attended'")

    enrol.status = "enrolled"
    enrol.checked_in_at = None
    enrol.checked_in_by = None
    db.commit()
    db.refresh(enrol)

    return {
        "message": "Check-in undone",
        "enrolment_id": enrol.id,
        "participant_name": enrol.participant_name,
        "participant_email": enrol.participant_email,
        "status": enrol.status,
        "restored_to": "enrolled",
    }


def list_training_checkin_preview_service(db: Session, tid: UUID, status_filter: str | None = None):
    from app.models.training_model import TrainingEnrolment

    _get_training_or_404(db, tid)
    q = db.query(TrainingEnrolment).filter(TrainingEnrolment.training_id == tid)
    if status_filter:
        q = q.filter(TrainingEnrolment.status == status_filter)
    rows = q.order_by(TrainingEnrolment.participant_name).all()

    out = []
    for r in rows:
        can = r.status in CHECKIN_ELIGIBLE_STATUSES
        if r.status == "attended":
            reason = "Already checked in"
        elif r.status == "cancelled":
            reason = "Cancelled — cannot check in"
        elif r.status == "waitlisted":
            reason = "Waitlisted — not yet enrolled"
        elif can:
            reason = "Ready to check in"
        else:
            reason = f"Status {r.status}"
        out.append({
            "enrolment_id": r.id,
            "participant_name": r.participant_name,
            "participant_email": r.participant_email,
            "status": r.status,
            "qr_code": r.qr_code,
            "checked_in_at": r.checked_in_at.isoformat() if r.checked_in_at else None,
            "checked_out_at": r.checked_out_at.isoformat() if r.checked_out_at else None,
            "can_check_in": can,
            "eligibility_reason": reason,
        })
    return out


def batch_check_in_training_enrolments_service(db: Session, tid: UUID, participants: list, current_user: dict | None = None):
    from datetime import datetime

    training = _get_training_or_404(db, tid)
    if training.status in ("cancelled", "archived"):
        raise HTTPException(status_code=400, detail=f"Cannot check-in: training is {training.status}")

    results = []
    succeeded = 0
    failed = 0
    for item in participants:
        enrolment_id = item.enrolment_id if hasattr(item, "enrolment_id") else item.get("enrolment_id")
        qr_code = item.qr_code if hasattr(item, "qr_code") else item.get("qr_code")
        enrol = _find_enrolment_by_id_or_qr(db, tid, enrolment_id, qr_code) if (enrolment_id or qr_code) else None
        if not enrol:
            results.append({
                "enrolment_id": str(enrolment_id or qr_code or ""),
                "status": "failed",
                "message": "Enrolment not found",
            })
            failed += 1
            continue
        if enrol.status == "cancelled":
            results.append({
                "enrolment_id": enrol.id,
                "participant_name": enrol.participant_name,
                "participant_email": enrol.participant_email,
                "status": "failed",
                "message": "Enrolment is cancelled",
            })
            failed += 1
            continue
        if enrol.status == "attended":
            results.append({
                "enrolment_id": enrol.id,
                "participant_name": enrol.participant_name,
                "participant_email": enrol.participant_email,
                "status": enrol.status,
                "checked_in_at": enrol.checked_in_at.isoformat() if enrol.checked_in_at else None,
                "message": "Already checked in",
            })
            succeeded += 1
            continue
        if enrol.status not in CHECKIN_ELIGIBLE_STATUSES:
            results.append({
                "enrolment_id": enrol.id,
                "participant_name": enrol.participant_name,
                "participant_email": enrol.participant_email,
                "status": "failed",
                "message": f"Cannot check-in: status is '{enrol.status}'",
            })
            failed += 1
            continue
        enrol.status = "attended"
        enrol.checked_in_at = datetime.utcnow()
        enrol.checked_in_by = current_user.get("id") if current_user else None
        results.append({
            "enrolment_id": enrol.id,
            "participant_name": enrol.participant_name,
            "participant_email": enrol.participant_email,
            "status": enrol.status,
            "checked_in_at": enrol.checked_in_at.isoformat() if enrol.checked_in_at else None,
            "message": "Checked in successfully",
        })
        succeeded += 1
    db.commit()
    return {"total": len(participants), "succeeded": succeeded, "failed": failed, "results": results}


def get_training_participant_dashboard_service(db: Session, tid: UUID, participant_email: str | None = None):
    from app.models.training_model import TrainingLiveSession

    training = _get_training_or_404(db, tid)
    enrol = _get_enrolment(db, tid, participant_email) if participant_email else None
    prog_data = get_training_progress_service(db, tid, participant_email=participant_email)

    recent_sessions = (
        db.query(TrainingLiveSession)
        .filter(TrainingLiveSession.training_id == tid)
        .order_by(TrainingLiveSession.scheduled_at.desc())
        .limit(5)
        .all()
    )
    return {
        "training_id": str(tid),
        "enrolment_status": enrol.status if enrol else "not_enrolled",
        "overall_percent": prog_data["overall_percent"],
        "sections_done": prog_data["sections_done"],
        "total_sections": prog_data["total_sections"],
        "lessons_done": prog_data["lessons_done"],
        "total_lessons": prog_data["total_lessons"],
        "certificate_url": prog_data["certificate_url"],
        "expired": prog_data["expired"],
        "recent_live_sessions": [
            {"id": str(s.id), "title": s.title, "scheduled_at": s.scheduled_at.isoformat(), "status": s.status}
            for s in recent_sessions
        ],
    }


def get_training_provider_dashboard_service(db: Session, tid: UUID):
    from sqlalchemy import func
    from app.models.training_model import TrainingEnrolment

    training = _get_training_or_404(db, tid)
    total = db.query(func.count(TrainingEnrolment.id)).filter(TrainingEnrolment.training_id == tid).scalar() or 0
    by_status_rows = (
        db.query(TrainingEnrolment.status, func.count(TrainingEnrolment.id))
        .filter(TrainingEnrolment.training_id == tid)
        .group_by(TrainingEnrolment.status)
        .all()
    )
    by_status = {r[0]: r[1] for r in by_status_rows}
    capacity_utilization = 0
    if training.capacity:
        try:
            cap = int(training.capacity)
            capacity_utilization = round(total / cap * 100, 2) if cap else 0
        except (TypeError, ValueError):
            pass
    recent = (
        db.query(TrainingEnrolment)
        .filter(TrainingEnrolment.training_id == tid)
        .order_by(TrainingEnrolment.created_at.desc())
        .limit(5)
        .all()
    )
    return {
        "training_id": str(tid),
        "total_enrolments": total,
        "by_status": by_status,
        "capacity_utilization": capacity_utilization,
        "recent_enrolments": [
            {"participant_email": r.participant_email, "status": r.status, "created_at": r.created_at.isoformat()}
            for r in recent
        ],
    }


def get_training_reports_service(db: Session, tid: UUID, report_type: str = "enrolment", date_from=None, date_to=None):
    from datetime import datetime

    from app.models.training_model import TrainingAssessmentSubmission, TrainingEnrolment, TrainingLiveSession, TrainingOrder, TrainingReview

    training = _get_training_or_404(db, tid)

    def _in_range(dt):
        if not dt:
            return True
        if date_from:
            try:
                if dt < datetime.fromisoformat(str(date_from)):
                    return False
            except (TypeError, ValueError):
                pass
        if date_to:
            try:
                if dt > datetime.fromisoformat(str(date_to)):
                    return False
            except (TypeError, ValueError):
                pass
        return True

    if report_type == "enrolment":
        rows = [r for r in db.query(TrainingEnrolment).filter(TrainingEnrolment.training_id == tid).all() if _in_range(r.created_at)]
        by_status: dict = {}
        for r in rows:
            by_status[r.status] = by_status.get(r.status, 0) + 1
        data = {"total": len(rows), "by_status": by_status}
    elif report_type == "attendance":
        sessions = db.query(TrainingLiveSession).filter(TrainingLiveSession.training_id == tid).all()
        total_attended = sum(len(s.attendance or []) for s in sessions)
        enrol_count = db.query(TrainingEnrolment).filter(TrainingEnrolment.training_id == tid).count()
        data = {
            "total_sessions": len(sessions),
            "total_attendance_marks": total_attended,
            "attendance_rate": round(total_attended / max(1, enrol_count * max(1, len(sessions))) * 100, 2),
        }
    elif report_type == "progress":
        data = get_training_progress_service(db, tid)
    elif report_type == "engagement":
        enrol_cnt = db.query(TrainingEnrolment).filter(TrainingEnrolment.training_id == tid).count()
        review_cnt = db.query(TrainingReview).filter(TrainingReview.training_id == tid).count()
        data = {"enrolments": enrol_cnt, "reviews": review_cnt, "engagement_rate": round(review_cnt / max(1, enrol_cnt) * 100, 2)}
    elif report_type == "assessment":
        submission_cnt = db.query(TrainingAssessmentSubmission).filter(TrainingAssessmentSubmission.training_id == tid).count()
        passed_cnt = db.query(TrainingAssessmentSubmission).filter(TrainingAssessmentSubmission.training_id == tid, TrainingAssessmentSubmission.passed.is_(True)).count()
        data = {"submissions": submission_cnt, "passed": passed_cnt, "pass_rate": round(passed_cnt / max(1, submission_cnt) * 100, 2) if submission_cnt else 0}
    elif report_type == "completion":
        data = get_training_progress_service(db, tid)
        data["completion_rate"] = data.get("overall_percent", 0)
    elif report_type == "revenue":
        rows = [r for r in db.query(TrainingOrder).filter(TrainingOrder.training_id == tid).all() if _in_range(r.created_at)]
        try:
            total_amount = sum(float(r.amount or 0) for r in rows)
        except (TypeError, ValueError):
            total_amount = 0
        data = {"total_revenue": str(total_amount), "currency": training.currency or "INR", "orders": len(rows)}
    else:
        data = {}
    return {"training_id": str(tid), "type": report_type, "data": data}


def get_training_summary_service(db: Session, enterprise_id: UUID | None = None):
    from sqlalchemy import func
    from app.models.training_model import Training, TrainingEnrolment

    q = db.query(Training).filter(Training.is_deleted.is_(False))
    if enterprise_id:
        q = q.filter(Training.enterprise_id == enterprise_id)
    status_rows = q.with_entities(Training.status, func.count(Training.id)).group_by(Training.status).all()
    by_status = {r[0]: r[1] for r in status_rows}
    cat_rows = q.with_entities(Training.category, func.count(Training.id)).group_by(Training.category).all()
    by_category = {r[0]: r[1] for r in cat_rows if r[0]}
    del_rows = q.with_entities(Training.delivery_mode, func.count(Training.id)).group_by(Training.delivery_mode).all()
    by_delivery = {r[0]: r[1] for r in del_rows if r[0]}
    total = sum(by_status.values())
    tids = [t.id for t in q.all()]
    total_enrol = 0
    if tids:
        total_enrol = db.query(func.count(TrainingEnrolment.id)).filter(TrainingEnrolment.training_id.in_(tids)).scalar() or 0
    return {
        "total_trainings": total,
        "by_status": by_status,
        "by_category": by_category,
        "by_delivery_mode": by_delivery,
        "total_enrolments": total_enrol,
    }


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
    _, session, _ = _resolve_live_attendance_target(db, tid, session_id)
    rows = _live_attendance_rows(session.get("attendance"))
    return {"session_id": str(session["id"]), "title": session.get("title"), "attendance": rows, "count": len(rows)}


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

def _enrolment_response_context(db: Session, t, tid: UUID, participant_email: str, status: str) -> dict:
    """Extra display/payment/session context the mobile app renders right
    after enrolling — attached onto the enrolment response, not persisted."""
    from datetime import datetime
    from app.models.training_model import TrainingLiveSession, TrainingOrder

    order = (
        db.query(TrainingOrder)
        .filter(TrainingOrder.training_id == tid, TrainingOrder.participant_email == participant_email)
        .order_by(TrainingOrder.created_at.desc())
        .first()
    )
    if order:
        amount_paid, currency, payment_status = order.amount, order.currency, order.payment_status
    elif t.price:
        amount_paid, currency, payment_status = t.price, t.currency, "pending"
    else:
        amount_paid, currency, payment_status = "0", t.currency, "free"

    next_session = (
        db.query(TrainingLiveSession)
        .filter(TrainingLiveSession.training_id == tid, TrainingLiveSession.scheduled_at >= datetime.utcnow(), TrainingLiveSession.status != "cancelled")
        .order_by(TrainingLiveSession.scheduled_at.asc())
        .first()
    )

    return {
        "training_title": t.title,
        "primary_image": t.primary_image,
        "delivery_mode": t.delivery_mode,
        "enterprise_name": t.enterprise.business_short_name if t.enterprise else None,
        "amount_paid": amount_paid,
        "currency": currency,
        "payment_status": payment_status,
        "message": "Enrolment pending approval" if status == "pending_approval" else "Enrolled successfully",
        "next_session": {"schedule": next_session.scheduled_at, "meeting_link": next_session.meeting_link} if next_session else None,
    }


def create_training_enrol_service(db: Session, tid: UUID, payload: dict, coupon_code: str | None = None, current_user: dict | None = None, *, commit: bool = True):
    from app.models.training_model import TrainingEnrolment
    from datetime import datetime, timedelta
    t = _get_training_or_404(db, tid)
    if t.status not in ("published", "approved"):
        raise HTTPException(status_code=400, detail=f"Training not open for enrolment (status={t.status})")
    _enforce_enrolment_window(t)
    # coupon validation — only enforced when the caller actually supplies a
    # code to redeem; a training having a promo coupon_code configured must
    # not block plain (non-discounted) enrolment.
    if coupon_code and t.coupon_code and coupon_code != t.coupon_code:
        raise HTTPException(status_code=400, detail="Invalid coupon code")
    import uuid as _uuid
    participant_email = payload.get("participant_email") or (current_user or {}).get("email")
    if not participant_email:
        raise HTTPException(status_code=400, detail="participant_email is required (send it in the request body, or authenticate with a session that carries an email claim)")
    participant_name = payload.get("participant_name") or (current_user or {}).get("name") or participant_email
    # dedup — block a second active enrolment for the same participant
    existing = db.query(TrainingEnrolment).filter(
        TrainingEnrolment.training_id == tid,
        TrainingEnrolment.participant_email == participant_email,
    ).first()
    if existing and existing.status in ENROLMENT_CAPACITY_STATUSES:
        raise HTTPException(status_code=400, detail="Already enrolled on this training")
    # capacity — when full, optionally auto-add the learner to the waitlist
    if t.capacity:
        try:
            cap = int(t.capacity)
            cnt = db.query(TrainingEnrolment).filter(TrainingEnrolment.training_id==tid, TrainingEnrolment.status.in_(ENROLMENT_CAPACITY_STATUSES)).count()
            if cnt >= cap:
                if payload.get("auto_waitlist"):
                    wl = join_waitlist_service(db, tid, {"participant_email": participant_email, "participant_name": participant_name}, current_user)
                    context = _enrolment_response_context(db, t, tid, participant_email, "waitlisted")
                    context.pop("message")  # keep the capacity-specific message below
                    return {"waitlisted": True, "position": wl.get("position"), "id": wl.get("id"), "training_id": str(tid), "message": f"Training at capacity ({cap}) — added to waitlist", **context}
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
    e = TrainingEnrolment(training_id=tid, participant_name=participant_name, participant_email=participant_email, group_enrol=payload.get("group_enrol", False), status=status, coupon_code=coupon_code, access_expires_at=expires, qr_code=str(_uuid.uuid4())[:12].upper())
    db.add(e)
    if not commit:
        db.flush()
        return e
    db.commit(); db.refresh(e)
    # group enrolment — create additional members if provided
    if payload.get("group_members"):
        for m in payload.get("group_members") or []:
            try:
                name=m.get("name") or m.get("participant_name") or participant_name
                email=m.get("email") or m.get("participant_email")
                if not email or email==participant_email:
                    continue
                extra=TrainingEnrolment(training_id=tid, participant_name=name, participant_email=email, group_enrol=True, status=status, coupon_code=coupon_code, access_expires_at=expires, qr_code=str(_uuid.uuid4())[:12].upper())
                db.add(extra)
            except: pass
        db.commit()
    # enrolment confirmation (in_app/push/email/sms stub)
    try:
        from app.services.notification_triggers import _safe_notify
        _safe_notify(db, f"training:{tid}", "training_enrolment_confirmation", {"training_id": str(tid), "status": status})
    except: pass
    for key, value in _enrolment_response_context(db, t, tid, participant_email, status).items():
        setattr(e, key, value)
    return e


def list_my_enrolments_service(db: Session, email: str, status_filter: str | None = None):
    """Participant dashboard rows — enough card data (title, image, vendor,
    price, progress) for mobile to render without a follow-up call per
    training."""
    from sqlalchemy.orm import joinedload

    from app.models.training_model import Training, TrainingEnrolment, TrainingProgress

    q = db.query(TrainingEnrolment).filter(TrainingEnrolment.participant_email == email)
    if status_filter:
        q = q.filter(TrainingEnrolment.status == status_filter)
    rows = q.order_by(TrainingEnrolment.created_at.desc()).all()

    training_ids = [r.training_id for r in rows]
    trainings_by_id: dict = {}
    progress_by_id: dict = {}
    if training_ids:
        trainings = (
            db.query(Training)
            .options(joinedload(Training.enterprise))
            .filter(Training.id.in_(training_ids))
            .all()
        )
        trainings_by_id = {t.id: t for t in trainings}
        progress_rows = (
            db.query(TrainingProgress)
            .filter(TrainingProgress.training_id.in_(training_ids), TrainingProgress.participant_email == email)
            .all()
        )
        progress_by_id = {p.training_id: p for p in progress_rows}

    out = []
    for r in rows:
        t = trainings_by_id.get(r.training_id)
        prog = progress_by_id.get(r.training_id)
        total_lessons = sum(len(s.get("lessons", []) or []) for s in (t.sections or [])) if t else 0
        completed_lessons = len(prog.lessons_completed or []) if prog else 0
        progress_percent = lesson_progress_percent(completed_lessons, total_lessons)
        out.append({
            "training_id": str(r.training_id),
            "status": r.status,
            "enrolment_id": str(r.id),
            "qr_code": r.qr_code if t and t.delivery_mode not in ("online", "recorded", "self_paced") else None,
            "qr_image_base64": _qr_image_base64(r.qr_code) if t and t.delivery_mode not in ("online", "recorded", "self_paced") else None,
            "created_at": r.created_at.isoformat(),
            "title": t.title if t else None,
            "primary_image": t.primary_image if t else None,
            "enterprise_name": (t.enterprise.business_short_name if t and t.enterprise else None),
            "delivery_mode": t.delivery_mode if t else None,
            "price": t.price if t else None,
            "currency": t.currency if t else None,
            "duration": t.duration if t else None,
            "progress_percent": progress_percent,
            "completed_lessons": completed_lessons,
            "total_lessons": total_lessons,
        })
    return out


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
    from app.models.training_model import TrainingOrder
    data = payload if isinstance(payload, dict) else payload.model_dump()
    t = _get_training_or_404(db, tid)
    coupon = data.get("coupon_code")
    try:
        # Reuse enrolment rules without committing; order and enrolment are atomic.
        enrol = create_training_enrol_service(db, tid, {
            "participant_name": data.get("participant_name"),
            "participant_email": data.get("participant_email"),
        }, coupon_code=coupon, commit=False)
        price = t.promo_price if (coupon and t.promo_price) else t.price or "0"
        quantity = data.get("quantity") or 1
        from decimal import Decimal
        amount = str(Decimal(str(price)) * int(quantity))
        order = TrainingOrder(training_id=tid, participant_name=enrol.participant_name,
            participant_email=enrol.participant_email, quantity=str(quantity), amount=amount,
            currency=t.currency or "INR", payment_status="confirmed", status="confirmed", coupon_code=coupon)
        db.add(order)
        db.commit()
        db.refresh(order)
    except Exception:
        db.rollback()
        raise
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
    return update_training_status_service(db, tid, "published", current_user)


def unpublish_training_service(db: Session, tid: UUID, current_user: dict | None = None):
    return update_training_status_service(db, tid, "unpublished", current_user)


def suspend_training_service(db: Session, tid: UUID, reason: str | None = None, current_user: dict | None = None):
    return update_training_status_service(db, tid, "suspended", current_user, notes=reason)


def cancel_training_service(db: Session, tid: UUID, reason: str | None = None, current_user: dict | None = None):
    return update_training_status_service(db, tid, "cancelled", current_user, notes=reason)


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
    return {"section_id": section_id, "section_title": (section or {}).get("title"), **{k: v for k, v in lesson.items() if k != "attendance"}}


def list_lesson_topics_service(db: Session, tid: UUID, section_id: str, lesson_id: str):
    training = _get_training_or_404(db, tid)
    _, lesson = _find_lesson(training, section_id, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    return lesson.get("topics") or []


def add_lesson_topic_service(db: Session, tid: UUID, section_id: str, lesson_id: str, payload):
    import uuid as _uuid
    from sqlalchemy.orm.attributes import flag_modified
    training = _get_training_or_404(db, tid)
    _, lesson = _find_lesson(training, section_id, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    data = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
    topics = list(lesson.get("topics") or [])
    new_topic = {"id": str(_uuid.uuid4()), **data}
    topics.append(new_topic)
    lesson["topics"] = topics
    flag_modified(training, "sections")
    db.commit()
    return new_topic


def update_lesson_topic_service(db: Session, tid: UUID, section_id: str, lesson_id: str, topic_id: str, payload):
    from sqlalchemy.orm.attributes import flag_modified
    training = _get_training_or_404(db, tid)
    _, lesson = _find_lesson(training, section_id, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    data = payload.model_dump(exclude_unset=True, mode="json") if hasattr(payload, "model_dump") else payload
    for topic in lesson.get("topics") or []:
        if topic.get("id") == topic_id:
            topic.update({k: v for k, v in data.items() if k != "id"})
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


def get_assessment_service(db: Session, tid: UUID, aid: str, current_user: dict | None = None):
    import copy
    from app.models.training_model import TrainingAssessmentSubmission
    training = _get_training_or_404(db, tid)
    for assessment in copy.deepcopy(training.assessments or []):
        if str(assessment.get("id")) == str(aid):
            role = current_user.get("role") if current_user else None
            if role not in ["admin", "provider"]:
                for q in assessment.get("questions", []):
                    q.pop("correct_answer", None)
                    q.pop("explanation", None)
            declared_limit = assessment.get("attempt_limit") or assessment.get("attempts_allowed")
            assessment["attempts_allowed"] = int(declared_limit) if declared_limit else None
            email = current_user.get("email") if current_user else None
            assessment["attempts_made"] = (
                db.query(TrainingAssessmentSubmission)
                .filter(TrainingAssessmentSubmission.training_id == tid, TrainingAssessmentSubmission.assessment_id == str(aid), TrainingAssessmentSubmission.participant_email == email)
                .count()
                if email else 0
            )
            return assessment
    raise HTTPException(status_code=404, detail="Assessment not found")


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


def _format_duration(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return f"{int(value)} min"
    return str(value)


def _section_summary(lessons: list) -> str:
    content_count = sum(1 for l in lessons if l.get("type") != "exam")
    quiz_count = sum(1 for l in lessons if l.get("type") == "exam")
    parts = [f"{content_count} lesson{'s' if content_count != 1 else ''}"]
    if quiz_count:
        parts.append(f"{quiz_count} quiz{'zes' if quiz_count != 1 else ''}")
    return " · ".join(parts)


def _extract_schedule(value):
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("datetime", "date", "start", "scheduled_at"):
            if value.get(key):
                return value.get(key)
    return None


_DEFAULT_LESSON_DETAIL_BY_TYPE = {
    "video": "Watch inside this session",
    "youtube": "Watch inside this session",
    "live": "Online live · tap to join",
    "venue": "Show QR at venue",
}


def _default_lesson_detail(ltype: str, is_locked: bool, is_completed: bool) -> str | None:
    if ltype == "exam":
        if is_locked:
            return "Unlocks after content lessons"
        return "View results" if is_completed else "Take the quiz"
    return _DEFAULT_LESSON_DETAIL_BY_TYPE.get(ltype)


def _base_lesson_payload(lesson: dict, is_locked: bool, is_completed: bool, completed_at: str | None, *, attended_at: str | None = None) -> dict:
    ltype = lesson.get("type") or "text"
    payload = {
        "id": lesson.get("id"),
        "type": ltype,
        "title": lesson.get("title"),
        "duration": _format_duration(lesson.get("duration")),
        "detail": (lesson.get("detail") if not is_locked else None) or _default_lesson_detail(ltype, is_locked, is_completed),
        "thumbnail_url": lesson.get("thumbnail_url"),
        "content_url": lesson.get("content_url") if not is_locked else None,
        "content": lesson.get("content") if not is_locked else None,
        "order": lesson.get("order"),
        "duration_minutes": lesson.get("duration_minutes"),
        "file_size": lesson.get("file_size"),
        "schedule": lesson.get("schedule"),
        "video_url": lesson.get("video_url") if not is_locked else None,
        "join_url": lesson.get("meeting_link") if not is_locked else None,
        "meeting_type": lesson.get("meeting_type"),
        "assessment_id": lesson.get("assessment_id"),
        "assignment_id": lesson.get("assignment_id"),
        "topics": lesson.get("topics") if not is_locked else None,
        "is_preview": bool(lesson.get("is_preview", False)),
        "is_mandatory": bool(lesson.get("is_mandatory", False)),
        "is_downloadable": bool(lesson.get("is_downloadable", False)),
        "is_locked": is_locked,
        "is_completed": is_completed,
        "completed_at": completed_at,
        "meeting_link": lesson.get("meeting_link") if not is_locked else None,
        "join_meta": lesson.get("join_meta") if not is_locked else None,
        "venue": lesson.get("venue"),
        "address": lesson.get("address"),
        "pass_code": lesson.get("pass_code") if not is_locked else None,
        "check_in_window": lesson.get("check_in_window"),
        "videos": lesson.get("videos") if not is_locked else None,
        "documents": lesson.get("documents") if not is_locked else None,
        "notes": lesson.get("notes") if not is_locked else None,
    }
    if ltype == "live":
        payload["is_attended"] = attended_at is not None
        payload["attended_at"] = attended_at
    return payload


def _build_exam_questions_for_learner(questions: list | None, *, include_answer: bool = False) -> list:
    """Allowlist transform — only known-safe fields are copied across, so a
    new grading key added to question storage later can't leak by default.
    Also normalizes plain-string options into {id, label} pairs (a, b, c, ...)."""
    import string

    out = []
    for q in questions or []:
        if not isinstance(q, dict):
            continue
        options = q.get("options")
        norm_options = None
        if isinstance(options, list) and options:
            if isinstance(options[0], dict):
                norm_options = [{"id": o.get("id"), "label": o.get("label", o.get("value"))} for o in options]
            else:
                letters = string.ascii_lowercase
                norm_options = [
                    {"id": letters[i] if i < len(letters) else str(i), "label": str(opt)}
                    for i, opt in enumerate(options)
                ]
        entry = {
            "id": q.get("id"),
            "question_text": q.get("question_text"),
            "question_type": q.get("question_type"),
            "points": q.get("points"),
            "options": norm_options,
            "explanation": q.get("explanation"),
        }
        if include_answer:
            entry["correct_answer"] = q.get("correct_answer")
        out.append(entry)
    return out


def _build_exam_assessment_payload(assessment: dict, submission, *, include_answer: bool = False) -> dict:
    score_percent = None
    if submission is not None and submission.score is not None:
        try:
            score_percent = int(submission.score)
        except (TypeError, ValueError):
            try:
                score_percent = float(submission.score)
            except (TypeError, ValueError):
                score_percent = None
    if score_percent is not None and assessment.get("score_unit") == "points":
        total = sum(float(q.get("points", 1)) for q in assessment.get("questions") or [])
        score_percent = round(min(100, max(0, score_percent / total * 100)), 2) if total else 0
    return {
        "id": assessment.get("id"),
        "type": assessment.get("type", "quiz"),
        "title": assessment.get("title"),
        "pass_percent": assessment.get("pass_percent"),
        "is_submitted": submission is not None,
        "score_percent": score_percent,
        "passed": submission.passed if submission is not None else None,
        "questions": _build_exam_questions_for_learner(assessment.get("questions"), include_answer=include_answer),
    }


def get_secure_training_content_service(db: Session, tid: UUID, current_user: dict):
    import copy

    from app.models.training_model import TrainingAssessmentSubmission, TrainingEnrolment, TrainingProgress

    email = current_user.get("email")
    role = current_user.get("role")
    is_staff = role in ("admin", "provider")
    enrol = db.query(TrainingEnrolment).filter(
        TrainingEnrolment.training_id == tid,
        TrainingEnrolment.participant_email == email,
        TrainingEnrolment.status.in_(ACTIVE_ENROLMENT_STATUSES),
    ).first() if email else None
    if not enrol and not is_staff:
        raise HTTPException(status_code=403, detail="Enrolled participants only")
    training = _get_training_or_404(db, tid)
    if training.status in ("draft", "cancelled", "archived") and not is_staff:
        raise HTTPException(status_code=403, detail=f"Content not available — training is {training.status}")

    from app.services.training_curriculum import normalize_curriculum
    curriculum = normalize_curriculum(training.sections, training.assessments, training.assignments)
    sections_raw = curriculum["sections"]
    assessments_by_id = {a["id"]: a for a in curriculum["assessments"]}
    from app.models.training_model import TrainingAssignmentSubmission
    assignment_submissions = {}
    if email:
        for submission in db.query(TrainingAssignmentSubmission).filter(
            TrainingAssignmentSubmission.training_id == tid,
            TrainingAssignmentSubmission.participant_email == email,
        ).order_by(TrainingAssignmentSubmission.submitted_at.desc()).all():
            assignment_submissions.setdefault(submission.assignment_id, submission)


    completed_lesson_ids: set = set()
    if email:
        prog = db.query(TrainingProgress).filter(
            TrainingProgress.training_id == tid,
            TrainingProgress.participant_email == email,
        ).first()
        if prog:
            completed_lesson_ids = set(prog.lessons_completed or [])

    attended_at_by_lesson_id: dict[str, str | None] = {}
    if email:
        for _sec in (training.sections or []):
            if _sec.get("type") == "live":
                for _rec in _live_attendance_rows(_sec.get("attendance")):
                    if _rec.get("participant_email") == email:
                        attended_at_by_lesson_id[str(_sec.get("id"))] = _rec.get("recorded_at")
            for _item in (_sec.get("lessons") or _sec.get("items") or []):
                if _item.get("type") == "live":
                    for _rec in _live_attendance_rows(_item.get("attendance")):
                        if _rec.get("participant_email") == email:
                            attended_at_by_lesson_id[str(_item.get("id"))] = _rec.get("recorded_at")

    submissions_by_assessment_id: dict = {}
    if email:
        subs = (
            db.query(TrainingAssessmentSubmission)
            .filter(TrainingAssessmentSubmission.training_id == tid, TrainingAssessmentSubmission.participant_email == email)
            .order_by(TrainingAssessmentSubmission.submitted_at.desc())
            .all()
        )
        for s in subs:
            submissions_by_assessment_id.setdefault(s.assessment_id, s)  # newest first, first-seen wins

    for section in sections_raw:
        for lesson in section["lessons"]:
            if lesson.get("assignment_id") in assignment_submissions:
                completed_lesson_ids.add(str(lesson["id"]))

    total_lessons = sum(len(s.get("lessons") or []) for s in sections_raw)
    completed_lessons_count = len(completed_lesson_ids)
    progress_percent = lesson_progress_percent(completed_lessons_count, total_lessons)

    out_sections = []
    prev_section_done = True  # section 0 is always unlocked
    prev_section_title = None
    for idx, section in enumerate(sections_raw):
        visible_lessons = [
            l for l in (section.get("lessons") or [])
            if not (isinstance(l, dict) and l.get("is_draft") and not l.get("is_preview"))
        ]

        section_unlocked = True if (idx == 0 or is_staff) else prev_section_done
        unlock_hint = None
        if not section_unlocked:
            unlock_hint = f"Complete {prev_section_title} quiz to unlock" if prev_section_title else "Complete the previous session to unlock"

        gating_lessons = [l for l in visible_lessons if l.get("is_mandatory")]
        content_ids_this_section = [str(l.get("id")) for l in gating_lessons if l.get("type") not in ("exam", "quiz")]
        content_done_this_section = all(cid in completed_lesson_ids for cid in content_ids_this_section) if content_ids_this_section else True
        has_exam = any(l.get("type") in ("exam", "quiz") for l in visible_lessons)
        section_exam_passed = not has_exam

        lessons_out = []
        for lesson in visible_lessons:
            ltype = lesson.get("type") or "text"
            accessible, _reason = _lesson_is_accessible(lesson, completed_lesson_ids, enrol, training)
            locked = (not section_unlocked) or (not accessible)

            if ltype in ("exam", "quiz"):
                aid = lesson.get("assessment_id")
                assessment = assessments_by_id.get(aid)
                submission = submissions_by_assessment_id.get(aid)
                if not is_staff:
                    locked = locked or (not content_done_this_section)
                if is_staff:
                    locked = False
                if submission is not None and submission.passed:
                    section_exam_passed = True
                is_completed = submission is not None
                completed_at = submission.submitted_at.isoformat() if submission else None
                lesson_payload = _base_lesson_payload(lesson, locked, is_completed, completed_at)
                lesson_payload["assessment"] = (
                    _build_exam_assessment_payload(assessment, submission, include_answer=is_staff)
                    if assessment and not locked
                    else None
                )
            else:
                if is_staff:
                    locked = False
                is_completed = str(lesson.get("id")) in completed_lesson_ids
                attended_at = attended_at_by_lesson_id.get(str(lesson.get("id"))) if ltype == "live" else None
                lesson_payload = _base_lesson_payload(lesson, locked, is_completed, None, attended_at=attended_at)
            lessons_out.append(lesson_payload)

        section_exam_passed = all(
            submissions_by_assessment_id.get(l.get("assessment_id")) is not None
            and submissions_by_assessment_id[l["assessment_id"]].passed
            for l in gating_lessons if l.get("type") in ("exam", "quiz")
        )
        prev_section_done = prev_section_done and content_done_this_section and section_exam_passed
        prev_section_title = section.get("title")

        sec_type = section.get("type", "section")
        sec_payload = {
            "id": section.get("id"),
            "type": sec_type,
            "order": section.get("order", idx + 1),
            "title": section.get("title"),
            "summary": _section_summary(visible_lessons),
            "schedule": _extract_schedule(section.get("schedule")),
            "is_unlocked": section_unlocked,
            "unlock_hint": unlock_hint,
            "lessons": lessons_out,
        }
        if sec_type == "live":
            sec_attended_at = attended_at_by_lesson_id.get(str(section.get("id")))
            sec_payload["is_attended"] = sec_attended_at is not None
            sec_payload["attended_at"] = sec_attended_at
            # Same learner QR + venue-mode gating already used for the top-level/venue-lesson
            # qr_code (see learning_response in training_curriculum.py) — same identifier, no
            # new QR is minted here, just an image encoding of the existing enrolment qr_code.
            section_qr = enrol.qr_code if enrol and training.delivery_mode in ("physical", "hybrid", "blended", "instructor_led") else None
            sec_payload["qr_code"] = section_qr
            sec_payload["qr_image_base64"] = _qr_image_base64(section_qr) if section_qr else None
        out_sections.append(sec_payload)

    enterprise_name = training.enterprise.business_short_name if getattr(training, "enterprise", None) else None

    result = {
        "training_id": str(tid),
        "title": training.title,
        "primary_image": training.primary_image,
        "enterprise_name": enterprise_name,
        "instructor_name": training.instructor_name,
        "delivery_mode": training.delivery_mode,
        "progress_percent": progress_percent,
        "completed_lessons": completed_lessons_count,
        "total_lessons": total_lessons,
        "qr_code": enrol.qr_code if enrol else None,
        "sections": out_sections,
    }

    from app.services.training_curriculum import learning_response
    return learning_response(result, curriculum, training, enrol, assignment_submissions)


def _require_enrolled_or_staff(db: Session, tid: UUID, current_user: dict):
    """Shared access gate: an enrolled participant, or admin/provider staff.
    Mirrors get_secure_training_content_service's gating exactly."""
    from app.models.training_model import TrainingEnrolment
    email = current_user.get("email")
    enrol = db.query(TrainingEnrolment).filter(
        TrainingEnrolment.training_id == tid,
        TrainingEnrolment.participant_email == email,
        TrainingEnrolment.status.in_(ACTIVE_ENROLMENT_STATUSES),
    ).first() if email else None
    if not enrol and current_user.get("role") not in ["admin", "provider"]:
        raise HTTPException(status_code=403, detail="Enrolled participants only")
    training = _get_training_or_404(db, tid)
    if training.status in ("draft", "cancelled", "archived") and current_user.get("role") not in ["admin", "provider"]:
        raise HTTPException(status_code=403, detail=f"Content not available — training is {training.status}")
    return training


def list_downloadable_lessons_service(db: Session, tid: UUID, current_user: dict) -> dict:
    """Offline-download manifest — a mobile app fetches this once to know
    which lesson content and notes it's allowed to cache for offline use."""
    training = _require_enrolled_or_staff(db, tid, current_user)
    if not training.offline_access_enabled:
        raise HTTPException(status_code=403, detail="Offline access is not enabled for this training")
    items = []
    for section in training.sections or []:
        for lesson in section.get("lessons", []):
            if not lesson.get("is_downloadable"):
                continue
            if lesson.get("is_draft") and not lesson.get("is_preview"):
                continue
            items.append({
                "section_id": section.get("id"), "section_title": section.get("title"),
                "lesson_id": lesson.get("id"), "title": lesson.get("title"),
                "type": lesson.get("type"), "content_url": lesson.get("content_url"),
            })

    notes = [
        {"title": n.get("title") or "Notes", "url": n.get("url")}
        for n in (getattr(training, "notes_documents", None) or [])
        if n.get("url")
    ]

    return {
        "training_id": str(tid),
        "downloadable_lessons": items,
        "count": len(items),
        "notes": notes,
        "notes_pdf_url": f"/api/v1/trainings/{tid}/notes.pdf",
    }


def get_lesson_download_service(db: Session, tid: UUID, section_id: str, lesson_id: str, current_user: dict) -> dict:
    training = _require_enrolled_or_staff(db, tid, current_user)
    if not training.offline_access_enabled:
        raise HTTPException(status_code=403, detail="Offline access is not enabled for this training")
    _section, lesson = _find_lesson(training, section_id, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    if not lesson.get("is_downloadable"):
        raise HTTPException(status_code=403, detail="This lesson is not marked available for offline download")
    if not lesson.get("content_url"):
        raise HTTPException(status_code=404, detail="This lesson has no content URL to download")
    return {
        "training_id": str(tid), "section_id": section_id, "lesson_id": lesson_id,
        "title": lesson.get("title"), "type": lesson.get("type"), "content_url": lesson.get("content_url"),
    }


def generate_training_notes_pdf_service(db: Session, tid: UUID, current_user: dict) -> bytes:
    """Auto-generated PDF summary — title, description, what-you'll-learn,
    requirements, audience, and the curriculum outline — for offline reading."""
    training = _require_enrolled_or_staff(db, tid, current_user)

    from io import BytesIO
    from xml.sax.saxutils import escape

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

    def _p(text: str | None, style) -> Paragraph | None:
        if not text:
            return None
        return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.75 * inch, bottomMargin=0.75 * inch)
    styles = getSampleStyleSheet()
    story = []

    story.append(_p(training.title or "Training Notes", styles["Title"]))
    meta_bits = [b for b in (training.category, training.level, training.language) if b]
    if meta_bits:
        story.append(_p(" · ".join(meta_bits), styles["Normal"]))
    if training.instructor_name:
        story.append(_p(f"Instructor: {training.instructor_name}", styles["Normal"]))
    story.append(Spacer(1, 0.2 * inch))

    for heading, value in (
        ("Description", training.description),
        ("Requirements", training.requirements),
        ("Who This Is For", training.target_audience),
    ):
        if value:
            story.append(_p(heading, styles["Heading2"]))
            story.append(_p(value, styles["Normal"]))
            story.append(Spacer(1, 0.15 * inch))

    objectives = getattr(training, "learning_objectives", None) or []
    if objectives:
        story.append(_p("What You'll Learn", styles["Heading2"]))
        story.append(ListFlowable(
            [ListItem(_p(obj, styles["Normal"])) for obj in objectives if obj],
            bulletType="bullet",
        ))
        story.append(Spacer(1, 0.15 * inch))

    sections = training.sections or []
    if sections:
        story.append(_p("Curriculum", styles["Heading2"]))
        for section in sections:
            title = section.get("title") or "Section"
            story.append(_p(title, styles["Heading3"]))
            lessons = section.get("lessons") or []
            if lessons:
                story.append(ListFlowable(
                    [ListItem(_p(l.get("title") or "Lesson", styles["Normal"])) for l in lessons],
                    bulletType="bullet",
                ))
            story.append(Spacer(1, 0.1 * inch))

    doc.build([item for item in story if item is not None])
    return buffer.getvalue()


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


# ---- Reviews (verified — must be enrolled) ----

def create_training_review_service(db: Session, tid: UUID, data):
    from app.models.training_model import TrainingEnrolment, TrainingReview

    _get_training_or_404(db, tid)
    enrolled = db.query(TrainingEnrolment).filter(
        TrainingEnrolment.training_id == tid,
        TrainingEnrolment.participant_email == data.participant_email,
        TrainingEnrolment.status.in_(ACTIVE_ENROLMENT_STATUSES),
    ).first()
    if not enrolled:
        raise HTTPException(status_code=403, detail="Verified reviews only — must be enrolled to review")

    existing = db.query(TrainingReview).filter(
        TrainingReview.training_id == tid,
        TrainingReview.participant_email == data.participant_email,
    ).first()
    if existing:
        existing.rating = str(data.rating)
        existing.comment = data.comment
        db.commit()
        db.refresh(existing)
        r = existing
    else:
        r = TrainingReview(training_id=tid, participant_email=data.participant_email, rating=str(data.rating), comment=data.comment)
        db.add(r)
        db.commit()
        db.refresh(r)

    return {
        "id": str(r.id), "training_id": str(r.training_id), "rating": int(r.rating),
        "comment": r.comment, "participant_email": r.participant_email,
        "verified": True, "created_at": r.created_at.isoformat(),
    }


def list_training_reviews_service(db: Session, tid: UUID):
    from app.models.training_model import TrainingReview

    _get_training_or_404(db, tid)
    rows = db.query(TrainingReview).filter(TrainingReview.training_id == tid).order_by(TrainingReview.created_at.desc()).all()
    reviews = [
        {"id": str(r.id), "training_id": str(r.training_id), "rating": int(r.rating), "comment": r.comment,
         "participant_email": r.participant_email, "verified": True, "created_at": r.created_at.isoformat()}
        for r in rows
    ]
    avg = round(sum(item["rating"] for item in reviews) / len(reviews), 2) if reviews else 0
    return {"reviews": reviews, "average_rating": avg, "count": len(reviews)}


# ---- Wishlist ----

def add_training_wishlist_service(db: Session, user_id: UUID, tid: UUID):
    from app.models.training_model import TrainingReview, TrainingWishlistItem

    training = _get_training_or_404(db, tid)
    existing = db.query(TrainingWishlistItem).filter(
        TrainingWishlistItem.user_id == user_id, TrainingWishlistItem.training_id == tid,
    ).first()
    if existing:
        item = existing
    else:
        item = TrainingWishlistItem(user_id=user_id, training_id=tid)
        db.add(item)
        db.commit()
        db.refresh(item)

    ratings = [int(r.rating) for r in db.query(TrainingReview).filter(TrainingReview.training_id == tid).all()]
    return {
        "id": str(item.id), "training_id": str(training.id), "title": training.title,
        "primary_image": training.primary_image, "price": training.price, "currency": training.currency,
        "average_rating": round(sum(ratings) / len(ratings), 2) if ratings else 0, "reviews_count": len(ratings),
        "added_at": item.created_at.isoformat(),
    }


def remove_training_wishlist_service(db: Session, user_id: UUID, tid: UUID):
    from app.models.training_model import TrainingWishlistItem

    item = db.query(TrainingWishlistItem).filter(
        TrainingWishlistItem.user_id == user_id, TrainingWishlistItem.training_id == tid,
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Not in wishlist")
    db.delete(item)
    db.commit()
    return {"message": "Removed from wishlist"}


def list_training_wishlist_service(db: Session, user_id: UUID):
    from app.models.training_model import TrainingReview, TrainingWishlistItem

    rows = db.query(TrainingWishlistItem).filter(TrainingWishlistItem.user_id == user_id).order_by(TrainingWishlistItem.created_at.desc()).all()
    items = []
    for item in rows:
        training = get_training_by_id(db, item.training_id, include_deleted=True)
        if not training:
            continue
        ratings = [int(r.rating) for r in db.query(TrainingReview).filter(TrainingReview.training_id == item.training_id).all()]
        items.append({
            "id": str(item.id), "training_id": str(training.id), "title": training.title,
            "primary_image": training.primary_image, "price": training.price, "currency": training.currency,
            "average_rating": round(sum(ratings) / len(ratings), 2) if ratings else 0, "reviews_count": len(ratings),
            "added_at": item.created_at.isoformat(),
        })
    return items


def _learner_enrolment(db: Session, tid: UUID, current_user: dict):
    from app.models.training_model import TrainingEnrolment
    email = current_user.get("email")
    if not email:
        return None
    return db.query(TrainingEnrolment).filter(
        TrainingEnrolment.training_id == tid,
        TrainingEnrolment.participant_email == email,
        TrainingEnrolment.status.in_(ACTIVE_ENROLMENT_STATUSES),
    ).first()


def get_learner_training_detail_service(db: Session, tid: UUID, current_user: dict):
    detail = get_training_service(db, tid, current_user).model_dump()
    if current_user.get("role") in ("admin", "provider", "super_admin"):
        return TrainingDetailResponse.model_validate(detail)
    enrolment = _learner_enrolment(db, tid, current_user)
    from app.services.training_curriculum import curriculum_preview
    detail["sections"] = curriculum_preview(detail.get("sections") or [])
    detail["assessments"] = []
    if not enrolment:
        detail["assignments"] = []
    for field in ("instructor_notes", "last_admin_notes", "rejection_reason"):
        detail[field] = None
    if enrolment:
        content = get_secure_training_content_service(db, tid, current_user)
        detail["sections"] = content["sections"]
        detail["assessments"] = content.get("assessments", [])
        detail["assignments"] = content.get("assignments", [])
    else:
        for field in ("meeting_link", "meeting_id", "meeting_passcode", "access_information",
                      "delivery_instructions", "pass_code", "qr_payload", "qr_image_base64",
                      "documents", "notes_documents", "notes", "notes_pdf_url"):
            detail[field] = None
    return TrainingDetailResponse.model_validate(detail)


def show_training_qr_service(db: Session, tid: UUID, current_user: dict):
    training = _get_training_or_404(db, tid)
    if getattr(training, "delivery_mode", None) in ("online", "recorded", "self_paced"):
        raise HTTPException(400, "QR is available only for venue-based training")
    enrolment = _learner_enrolment(db, tid, current_user)
    if not enrolment:
        raise HTTPException(403, "Enrolled participants only")
    validate_training_qr_service(db, tid, enrolment.qr_code)
    return {"training_id": str(tid), "qr_code": enrolment.qr_code,
            "qr_image_base64": _qr_image_base64(enrolment.qr_code)}


def toggle_training_wishlist_service(db: Session, user_id: UUID, tid: UUID):
    from app.models.training_model import TrainingWishlistItem
    _get_training_or_404(db, tid)
    item = db.query(TrainingWishlistItem).filter(
        TrainingWishlistItem.user_id == user_id, TrainingWishlistItem.training_id == tid,
    ).first()
    if item:
        db.delete(item)
    else:
        db.add(TrainingWishlistItem(user_id=user_id, training_id=tid))
    db.commit()
    return {"training_id": str(tid), "wishlisted": item is None,
            "message": "Removed from wishlist" if item else "Added to wishlist"}


def export_training_enrolments_service(db: Session, tid: UUID, current_user: dict):
    import csv
    import io
    from app.models.training_model import TrainingEnrolment
    from app.repository.training_repo import require_training_owner

    require_training_owner(db, tid, current_user)
    rows = db.query(TrainingEnrolment).filter(TrainingEnrolment.training_id == tid).order_by(TrainingEnrolment.created_at, TrainingEnrolment.id).all()
    output = io.StringIO()
    writer = csv.writer(output)
    columns = ("id", "training_id", "participant_name", "participant_email", "status", "group_enrol", "created_at")
    writer.writerow(columns)
    for row in rows:
        values = []
        for key in columns:
            value = getattr(row, key)
            value = value.isoformat() if hasattr(value, "isoformat") else str(value) if value is not None else ""
            # Keep user-entered cells from executing as spreadsheet formulas.
            if value.startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
                value = "'" + value
            values.append(value)
        writer.writerow(values)
    return output.getvalue()
