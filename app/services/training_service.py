from uuid import UUID
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.models.enterprise_model import Enterprise
from app.models.location_model import EnterpriseLocation
from app.repository.training_repo import create_training, delete_training, get_training_by_id, get_trainings, update_training
from app.repository.query_utils import build_pagination_meta
from app.schemas.training_schema import TrainingDetailResponse, TrainingListItemResponse, TrainingPaginatedResponse, TrainingResponse
from app.services.response_mappers import _qr_image_base64, map_training_detail, map_training_list_item, map_training_write
from app.services import review_audit
from app.services.review_common import (
    ListOptions,
    apply_list_options,
    average_rating,
    average_rating as review_average,  # get_training_summary_service has a local named average_rating
    clean_comment,
    parse_rating,
    public_name,
    rating_distribution,
    validate_action,
)

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
    if obj.status == "pending_approval":  # created straight into the approval queue
        try:
            from app.services import training_workflow_notifications
            training_workflow_notifications.notify_training_approval(obj)
        except Exception:
            pass
    return TrainingResponse.model_validate(map_training_write(obj))

def get_trainings_service(db: Session, **kw):
    items, total = get_trainings(db, **kw)

    stats = _approved_review_stats(db, [i.id for i in items])

    mapped = []
    for i in items:
        d = map_training_list_item(i)
        d["average_rating"], d["reviews_count"] = stats.get(i.id, (None, 0))
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
    approved = [
        r for r in db.query(TrainingReview).filter(
            TrainingReview.training_id == tid, TrainingReview.moderation_status == "approved"
        ).all()
        if parse_rating(r.rating) is not None
    ]
    detail["average_rating"] = average_rating(parse_rating(r.rating) for r in approved)
    detail["reviews_count"] = len(approved)
    recent_reviews = sorted(approved, key=lambda r: r.created_at, reverse=True)[:50]
    names = _reviewer_names(db, tid, {r.participant_email for r in recent_reviews})
    detail["reviews"] = [
        {
            "id": r.id,
            "training_id": r.training_id,
            "participant_name": names.get(r.participant_email),
            "rating": parse_rating(r.rating),
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

def update_training_status_service(db: Session, tid: UUID, st: str, current_user: dict | None = None, notes: str | None = None, access_token: str | None = None):
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
    first_publish = st == "published" and obj.published_at is None
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
    db.commit(); db.refresh(obj)
    if st in ("pending_approval", "approved", "rejected", "needs_revision"):
        try:
            from app.services import training_workflow_notifications
            training_workflow_notifications.notify_training_approval(obj, reason=notes, access_token=access_token)
        except Exception:
            pass
    if first_publish:  # not on re-publish after an unpublish/suspend — users were already told once
        try:
            from app.services import training_notifications
            training_notifications.notify_new_training(obj, exclude_user_id=(current_user or {}).get("id"), access_token=access_token)
        except Exception:
            pass
    return TrainingResponse.model_validate(map_training_write(obj))


def restore_training_service(db: Session, tid: UUID, current_user: dict | None = None, access_token: str | None = None):
    """Restore an archived training back to draft (clears is_deleted)."""
    return update_training_status_service(db, tid, "draft", current_user, access_token=access_token)

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
    # enrolment_start/enrolment_end are stored as naive datetimes representing
    # wall-clock time in training.time_zone (not UTC) — the same convention
    # app.utils.event_utils already solves for events, so reuse its helpers
    # (they're generic: they only read time_zone-style attrs via getattr).
    from app.utils.event_utils import get_event_timezone, _get_utc_now, _localize_and_convert

    now_utc = _get_utc_now()
    tz = get_event_timezone(training)
    # Display name reflects the *resolved* zone, not the raw stored string —
    # an invalid time_zone value falls back to UTC, so the message should too.
    tz_name = getattr(tz, "key", "UTC")

    start_utc = _localize_and_convert(training.enrolment_start, tz)
    end_utc = _localize_and_convert(training.enrolment_end, tz)

    if start_utc and now_utc < start_utc:
        opens_at = training.enrolment_start.replace(tzinfo=tz).isoformat()
        raise HTTPException(status_code=400, detail=f"Enrolment not yet open (opens {opens_at} ({tz_name}))")
    if end_utc and now_utc > end_utc:
        closes_at = training.enrolment_end.replace(tzinfo=tz).isoformat()
        raise HTTPException(status_code=400, detail=f"Enrolment closed (closed {closes_at} ({tz_name}))")


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


def _promote_waitlist(db: Session, tid: UUID, access_token: str | None = None) -> dict | None:
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
    from app.services.training_learner_identity import resolve_learner_user_id
    promoted = TrainingEnrolment(
        training_id=tid,
        participant_name=next_wait.participant_name,
        participant_email=next_wait.participant_email,
        # kept from when they joined the waitlist; an older entry without one is recovered from the same
        # person's other records
        user_id=next_wait.user_id or resolve_learner_user_id(db, next_wait, training, access_token, persist=False),
        status=status,
        qr_code=str(_uuid.uuid4())[:12].upper(),
        access_expires_at=expires,
    )
    db.add(promoted)
    db.delete(next_wait)
    db.commit()
    db.refresh(promoted)
    if promoted.status == "enrolled":
        try:
            from app.services import training_workflow_notifications
            training_workflow_notifications.notify_enrollment_confirmed_to_admins(training, promoted, access_token=access_token)
        except Exception:
            pass
    return {"enrolment_id": str(promoted.id), "participant_email": promoted.participant_email, "status": promoted.status}


def join_waitlist_service(db: Session, tid: UUID, payload: dict | None = None, current_user: dict | None = None, access_token: str | None = None):
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
    if existing_enrol and existing_enrol.status not in ("cancelled", "rejected", "expired"):
        raise HTTPException(status_code=400, detail="Already enrolled on this training")
    existing_wait = db.query(TrainingWaitlist).filter(
        TrainingWaitlist.training_id == tid,
        TrainingWaitlist.participant_email == participant_email,
    ).first()
    if existing_wait:
        raise HTTPException(status_code=400, detail="Already on the waitlist for this training")
    from app.services.training_learner_identity import resolve_enrolling_user_id
    w = TrainingWaitlist(training_id=tid, participant_name=participant_name, participant_email=participant_email,
                         user_id=resolve_enrolling_user_id(current_user, participant_email, access_token))
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


def leave_waitlist_service(db: Session, tid: UUID, entry_id: UUID, participant_email: str | None = None, access_token: str | None = None):
    from app.models.training_model import TrainingWaitlist
    q = db.query(TrainingWaitlist).filter(TrainingWaitlist.id == entry_id, TrainingWaitlist.training_id == tid)
    if participant_email:
        q = q.filter(TrainingWaitlist.participant_email == participant_email)
    w = q.first()
    if not w:
        raise HTTPException(status_code=404, detail="Waitlist entry not found")
    db.delete(w)
    db.commit()
    promoted = _promote_waitlist(db, tid, access_token)
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

def _flat_lesson_order(training) -> list[tuple[str, str]]:
    """[(section_id, lesson_id), ...] in curriculum order."""
    return [
        (section.get("id"), lesson.get("id"))
        for section in training.sections or []
        for lesson in section.get("lessons", [])
        if lesson.get("id")
    ]


def _resume_lesson_from_progress(training, prog) -> tuple[str | None, str | None]:
    """'Continue where you left off' target:
    - If the most-recently-accessed lesson isn't complete yet, resume exactly
      there (mid-video case).
    - If it IS complete (or nothing has been touched yet), advance to the
      first incomplete lesson in curriculum order.
    - None/None once nothing incomplete remains."""
    completed = set((prog.lessons_completed or [])) if prog else set()
    positions = (prog.lesson_positions or {}) if prog else {}

    if positions:
        candidates = [(lesson_id, data) for lesson_id, data in positions.items() if data.get("last_accessed_at")]
        if candidates:
            last_touched, data = max(candidates, key=lambda kv: kv[1]["last_accessed_at"])
            if last_touched not in completed:
                return data.get("section_id"), last_touched

    for section_id, lesson_id in _flat_lesson_order(training):
        if lesson_id not in completed:
            return section_id, lesson_id
    return None, None


def save_lesson_progress_service(db: Session, tid: UUID, lesson_id: str, payload, participant_email: str) -> dict:
    from datetime import datetime
    from app.models.training_model import TrainingProgress

    t = _get_training_or_404(db, tid)
    enrol = _get_enrolment(db, tid, participant_email)
    if not _is_active_enrolment(enrol):
        raise HTTPException(status_code=403, detail="Active enrolment required")

    target_section_id = None
    found = False
    for section in t.sections or []:
        for lesson in section.get("lessons", []):
            if lesson.get("id") == lesson_id:
                target_section_id = section.get("id")
                found = True
                break
        if found:
            break
    if not found:
        raise HTTPException(status_code=404, detail="Lesson not found")

    section_id = payload.section_id or target_section_id
    position_seconds = payload.position_seconds
    duration_seconds = payload.duration_seconds
    progress_percent = round(position_seconds / duration_seconds * 100, 2) if duration_seconds else 0.0

    prog = db.query(TrainingProgress).filter(
        TrainingProgress.training_id == tid,
        TrainingProgress.participant_email == participant_email,
    ).first()
    if not prog:
        prog = TrainingProgress(
            training_id=tid, participant_email=participant_email,
            sections_completed=[], lessons_completed=[], overall_percent="0", lesson_positions={},
        )
        db.add(prog); db.commit(); db.refresh(prog)

    now = datetime.utcnow()
    now_iso = now.isoformat()
    positions = dict(prog.lesson_positions or {})
    positions[lesson_id] = {
        "section_id": section_id,
        "position_seconds": position_seconds,
        "duration_seconds": duration_seconds,
        "last_accessed_at": now_iso,
    }
    prog.lesson_positions = positions
    prog.last_accessed_at = now
    db.commit(); db.refresh(prog)

    is_completed = lesson_id in (prog.lessons_completed or [])
    return {
        "training_id": str(tid),
        "section_id": section_id,
        "lesson_id": lesson_id,
        "position_seconds": position_seconds,
        "duration_seconds": duration_seconds,
        "progress_percent": progress_percent,
        "is_completed": is_completed,
        "last_accessed_at": now_iso,
    }


def get_training_progress_service(db: Session, tid: UUID, participant_email: str | None = None):
    t = _get_training_or_404(db, tid)
    sections = t.sections or []
    total_sections = len(sections)
    total_lessons = sum(len(s.get("lessons", [])) for s in sections)
    completed_sections = []
    completed_lessons = []
    certificate_url = None
    prog = None
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
    resume_section_id, resume_lesson_id = _resume_lesson_from_progress(t, prog)
    positions = (prog.lesson_positions or {}) if prog else {}
    lessons_positions = [
        {
            "lesson_id": lesson_id,
            "section_id": data.get("section_id"),
            "position_seconds": data.get("position_seconds"),
            "duration_seconds": data.get("duration_seconds"),
            "is_completed": lesson_id in completed_lessons,
            "last_accessed_at": data.get("last_accessed_at"),
        }
        for lesson_id, data in positions.items()
    ]
    return {
        "training_id": str(tid),
        "overall_percent": overall,
        "progress_percent": overall,
        "sections_done": sections_done,
        "total_sections": total_sections,
        "lessons_done": lessons_done,
        "completed_lessons": lessons_done,
        "total_lessons": total_lessons,
        "certificate_url": certificate_url,
        "sections_detail": sections_detail,
        "lessons_detail": lessons_detail,
        "resume_section_id": resume_section_id,
        "resume_lesson_id": resume_lesson_id,
        "lessons": lessons_positions,
        "expired": expired,
        "status": "expired" if expired else "active",
        "access_expires_at": expires_at.isoformat() if expires_at else None,
    }

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

def _apply_lesson_completion(db: Session, tid: UUID, lesson_id: str, participant_email: str, t=None, *, attendance_source: bool = False) -> dict:
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
    was_completed = lesson_id in lessons
    lessons.add(lesson_id)
    prog.lessons_completed = list(lessons)
    # Who owns this completion: attendance (a roster tick / QR scan) only when it is the one that created it. A
    # lesson that was already complete stays the learner's, and when the learner completes a lesson that
    # attendance had completed, it becomes theirs, so reversing the attendance later no longer undoes it.
    by_attendance = list(prog.attendance_completed_lessons or [])
    if attendance_source and not was_completed and lesson_id not in by_attendance:
        by_attendance.append(lesson_id)
    elif not attendance_source and lesson_id in by_attendance:
        by_attendance.remove(lesson_id)
    prog.attendance_completed_lessons = by_attendance
    # The lessons are read the way /content reads them (see _completion_curriculum), so what counts as "all done"
    # here is what the learner sees as 100%.
    curriculum = _completion_curriculum(t)
    target_section_id = curriculum["section_of"].get(lesson_id)
    if target_section_id:
        section_lessons = curriculum["lessons_by_section"].get(target_section_id) or []
        if section_lessons and all(lid in lessons for lid in section_lessons):
            completed_sections.add(target_section_id)
            prog.sections_completed = list(completed_sections)
    summary = _completion_summary(curriculum, lessons)
    total, mandatory_done, mandatory_total, overall = (
        summary["total_lessons"], summary["mandatory_done"], summary["mandatory_total"], summary["overall_percent"],
    )
    prog.overall_percent=str(overall)
    prog.last_accessed_at=datetime.utcnow()
    # completion when every lesson is done or every mandatory lesson is done
    newly_completed = False
    if summary["complete"]:
        newly_completed = prog.completed_at is None
        if newly_completed:
            prog.completed_at = datetime.utcnow()
        prog.certificate_url=f"/api/v1/trainings/{tid}/certificate.pdf?participant_email={participant_email}"
    db.commit(); db.refresh(prog)
    if newly_completed:  # first completion only — later lesson saves must not re-send the certificate email
        try:
            from app.services import training_notifications
            enrol = _get_enrolment(db, tid, participant_email)
            training_notifications.notify_certificate_ready(
                t, email=participant_email, name=getattr(enrol, "participant_name", None),
                user_id=getattr(enrol, "user_id", None), certificate_url=prog.certificate_url,
            )
        except Exception:
            pass
    return {"overall_percent": overall, "lessons_done": summary["lessons_done"], "total_lessons": total, "mandatory_done": mandatory_done, "mandatory_total": mandatory_total, "completed_at": prog.completed_at.isoformat() if prog.completed_at else None, "certificate_url": prog.certificate_url}


def _completion_curriculum(t) -> dict:
    """The lessons that count towards completing a training, read the same way GET /content reads them.

    /content normalises the curriculum (a section's lessons may be stored under "lessons" or "items"); completion
    used to read only the raw "lessons" key, so for a training stored the other way it saw no lessons at all and a
    learner who finished everything never got their certificate. Falls back to the raw sections when they cannot
    be normalised."""
    try:
        from app.services.training_curriculum import normalize_curriculum
        sections = normalize_curriculum(
            t.sections, getattr(t, "assessments", None), getattr(t, "assignments", None)
        )["sections"]
    except Exception:
        sections = [s for s in (t.sections or []) if isinstance(s, dict)]
    lesson_ids: list[str] = []
    mandatory_ids: set[str] = set()
    section_of: dict[str, str] = {}
    lessons_by_section: dict[str, list[str]] = {}
    assignment_of: dict[str, str] = {}
    for section in sections:
        section_id = section.get("id")
        for lesson in section.get("lessons") or section.get("items") or []:
            lesson_id = lesson.get("id")
            if not lesson_id:
                continue
            lesson_id = str(lesson_id)
            lesson_ids.append(lesson_id)
            lessons_by_section.setdefault(section_id, []).append(lesson_id)
            section_of[lesson_id] = section_id
            if lesson.get("assignment_id"):
                assignment_of[lesson_id] = str(lesson["assignment_id"])
            if lesson.get("is_mandatory") or lesson.get("completion_rule") == "mandatory":
                mandatory_ids.add(lesson_id)
    return {
        "lesson_ids": lesson_ids,
        "mandatory_ids": mandatory_ids,
        "section_of": section_of,
        "lessons_by_section": lessons_by_section,
        "assignment_of": assignment_of,
    }


def issue_earned_certificate(db: Session, tid: UUID, participant_email: str, *, notify: bool = True, dry_run: bool = False) -> bool:
    """Issue the certificate to a learner who has finished the training but never got one.

    A certificate was only ever issued at the moment a lesson was completed through the normal completion path. A
    learner whose last lessons were recorded some other way (an older check-in on a venue/live lesson, a count that
    disagreed with /content, a lesson list stored under "items") showed 100% in /content and still had no
    certificate. This re-checks the learner's saved progress by the same rules /content shows and issues it.
    Returns True when a certificate was issued; changes nothing for a learner who has not finished, has no active
    enrolment, or already has one."""
    from app.models.training_model import TrainingAssignmentSubmission, TrainingProgress

    prog = db.query(TrainingProgress).filter(
        TrainingProgress.training_id == tid,
        TrainingProgress.participant_email == participant_email,
    ).first()
    if not prog or prog.certificate_url:
        return False
    enrol = _get_enrolment(db, tid, participant_email)
    if not _is_active_enrolment(enrol):
        return False

    t = _get_training_or_404(db, tid)
    curriculum = _completion_curriculum(t)
    completed = {str(x) for x in (prog.lessons_completed or [])}
    # /content also counts a lesson as done once its assignment has been submitted.
    submitted = {
        str(s.assignment_id)
        for s in db.query(TrainingAssignmentSubmission).filter(
            TrainingAssignmentSubmission.training_id == tid,
            TrainingAssignmentSubmission.participant_email == participant_email,
        ).all()
    }
    completed |= {lesson_id for lesson_id, assignment_id in curriculum["assignment_of"].items() if assignment_id in submitted}
    summary = _completion_summary(curriculum, completed)
    if not summary["complete"]:
        return False
    if dry_run:
        return True  # would be issued; nothing written

    from datetime import datetime
    newly_completed = prog.completed_at is None
    prog.lessons_completed = sorted(completed | {str(x) for x in (prog.lessons_completed or [])})
    prog.overall_percent = str(summary["overall_percent"])
    if newly_completed:
        prog.completed_at = datetime.utcnow()
    prog.certificate_url = f"/api/v1/trainings/{tid}/certificate.pdf?participant_email={participant_email}"
    db.commit()
    db.refresh(prog)
    if notify and newly_completed:
        try:
            from app.services import training_notifications
            training_notifications.notify_certificate_ready(
                t, email=participant_email, name=getattr(enrol, "participant_name", None),
                user_id=getattr(enrol, "user_id", None), certificate_url=prog.certificate_url,
            )
        except Exception:
            pass
    return True


def _completion_summary(curriculum: dict, completed) -> dict:
    """Totals for a learner's completed lesson ids. Only ids that are lessons of this training count, so an id
    left over from a lesson that was removed, or from a check-in on a whole session, cannot push the percentage
    past 100 and stop the course from ever counting as complete."""
    lesson_ids = set(curriculum["lesson_ids"])
    completed = {str(c) for c in (completed or [])}
    counted = completed & lesson_ids
    total = len(curriculum["lesson_ids"]) or 1
    mandatory_ids = curriculum["mandatory_ids"]
    mandatory_done = len(mandatory_ids & completed)
    mandatory_total = len(mandatory_ids)
    overall = round(min(len(counted) / total * 100, 100), 2)
    return {
        "total_lessons": total,
        "lessons_done": len(counted),
        "mandatory_done": mandatory_done,
        "mandatory_total": mandatory_total,
        "overall_percent": overall,
        "complete": bool(lesson_ids) and (overall >= 100 or (mandatory_total > 0 and mandatory_done == mandatory_total)),
    }


# ---- Attendance -> lesson completion ----
# Marking a learner "attended" on a lesson (manual roster or QR scan) completes that lesson for them. Only
# "attended" does: "absent" and "not_marked" never complete anything. Reversing it undoes only the completion that
# attendance created (progress.attendance_completed_lessons), never one the learner earned themselves.
# Quizzes/exams are not completed by attendance: they complete by submitting the assessment, which is what
# /content reads for them, so an attendance tick must not count as a pass.

_NOT_COMPLETED_BY_ATTENDANCE_TYPES = ("exam", "quiz")


def _lesson_completes_on_attendance(lesson: dict) -> bool:
    return (lesson.get("type") or "") not in _NOT_COMPLETED_BY_ATTENDANCE_TYPES


def _progress_summary(prog, t) -> dict:
    summary = _completion_summary(_completion_curriculum(t), prog.lessons_completed if prog else [])
    return {
        "overall_percent": summary["overall_percent"],
        "lessons_done": summary["lessons_done"],
        "total_lessons": summary["total_lessons"],
        "mandatory_done": summary["mandatory_done"],
        "mandatory_total": summary["mandatory_total"],
        "completed_at": prog.completed_at if prog else None,
    }


def _attendance_completion_views(db: Session, tid: UUID, training, lesson_id: str, enrolments: list) -> dict:
    """{enrolment id: {is_completed, completed_by_attendance, progress}} for the people on an attendance response."""
    from app.models.training_model import TrainingProgress

    emails = [e.participant_email for e in enrolments]
    progress_by_email = {}
    if emails:
        progress_by_email = {
            p.participant_email: p
            for p in db.query(TrainingProgress).filter(
                TrainingProgress.training_id == tid, TrainingProgress.participant_email.in_(emails)
            ).all()
        }
    lesson_id = str(lesson_id)
    views = {}
    for enrol in enrolments:
        prog = progress_by_email.get(enrol.participant_email)
        done = lesson_id in set(prog.lessons_completed or []) if prog else False
        views[enrol.id] = {
            "is_completed": done,
            "completed_by_attendance": done and lesson_id in set(prog.attendance_completed_lessons or []),
            "progress": _progress_summary(prog, training),
        }
    return views


def _revoke_attendance_completion(db: Session, tid: UUID, lesson_id: str, participant_email: str, t=None) -> bool:
    """Undo a completion that attendance created and recompute the learner's totals. Returns False and changes
    nothing when the completion is not attendance's (the learner earned it, or there is none)."""
    from app.models.training_model import TrainingProgress

    t = t or _get_training_or_404(db, tid)
    prog = db.query(TrainingProgress).filter(
        TrainingProgress.training_id == tid,
        TrainingProgress.participant_email == participant_email,
    ).first()
    lesson_id = str(lesson_id)
    by_attendance = list(prog.attendance_completed_lessons or []) if prog else []
    if not prog or lesson_id not in by_attendance:
        return False

    by_attendance.remove(lesson_id)
    prog.attendance_completed_lessons = by_attendance
    lessons = set(prog.lessons_completed or [])
    lessons.discard(lesson_id)
    prog.lessons_completed = list(lessons)

    curriculum = _completion_curriculum(t)
    completed_sections = set(prog.sections_completed or [])
    completed_sections.discard(curriculum["section_of"].get(lesson_id))
    prog.sections_completed = list(completed_sections)

    summary = _completion_summary(curriculum, lessons)
    prog.overall_percent = str(summary["overall_percent"])
    if prog.completed_at and not summary["complete"]:
        # The course only counted as complete because of this attendance; it no longer is.
        prog.completed_at = None
        prog.certificate_url = None
    db.commit()
    return True


def _sync_attendance_completion(db: Session, tid: UUID, training, lesson: dict, enrol, new_status: str | None, previous_status: str | None) -> None:
    """Called after an attendance status is saved. attended -> the lesson is complete (idempotent);
    attended -> anything else -> undo what attendance created. absent / not_marked complete nothing."""
    from app.models.training_model import TrainingProgress

    if not _lesson_completes_on_attendance(lesson):
        return
    lesson_id = str(lesson.get("id"))
    if new_status == "attended":
        prog = db.query(TrainingProgress).filter(
            TrainingProgress.training_id == tid,
            TrainingProgress.participant_email == enrol.participant_email,
        ).first()
        if prog and lesson_id in set(prog.lessons_completed or []):
            return  # already complete (an earlier attendance, or the learner's own): nothing to change
        _apply_lesson_completion(db, tid, lesson_id, enrol.participant_email, t=training, attendance_source=True)
    elif previous_status == "attended":
        _revoke_attendance_completion(db, tid, lesson_id, enrol.participant_email, t=training)


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
        # A training whose lessons are stored under "items" (which /content reads) has none under "lessons".
        _, target_lesson = _find_lesson_any_type(t, lesson_id)
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
        if str(section.get("id")) == str(session_uuid) and section.get("type") in ("live", "venue"):
            return training, section, True
        for item in section["lessons"]:
            if str(item["id"]) == str(session_uuid) and item["type"] in ("live", "venue"):
                return training, item, True
    raise HTTPException(404, "Live session not found")


def _resolve_lesson_attendance_target(db: Session, tid: UUID, lesson_id: str):
    """Lesson-wise attendance: only matches a lesson nested inside a section
    (not the section itself, and not the standalone TrainingLiveSession table —
    those stay served by the session-wise /live-sessions/{session_id}/attendance)."""
    from app.services.training_curriculum import normalize_curriculum
    try:
        lesson_uuid = UUID(str(lesson_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "Invalid lesson_id") from exc
    training = _get_training_or_404(db, tid)
    curriculum = normalize_curriculum(training.sections, training.assessments, training.assignments)
    for section in curriculum["sections"]:
        for item in section["lessons"]:
            if str(item["id"]) == str(lesson_uuid) and item.get("type") in ("live", "venue"):
                return training, item, True
    raise HTTPException(404, "Lesson not found")


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


def _record_attendance_for_target(db: Session, tid: UUID, owner, target: dict, embedded: bool, participant_email: str, *, id_field: str):
    from app.models.training_model import TrainingProgress
    from datetime import datetime

    enrol = _get_enrolment(db, tid, participant_email)
    if not _is_active_enrolment(enrol):
        raise HTTPException(status_code=403, detail="Active enrolment required for attendance")

    attendance = _live_attendance_rows(target.get("attendance"))
    existing = next((a for a in attendance if a.get("participant_email") == participant_email), None)
    recorded_at = existing.get("recorded_at") if existing else datetime.utcnow().isoformat()
    if not existing:
        attendance.append({"participant_email": participant_email, "recorded_at": recorded_at})
        _save_live_attendance(owner, target["id"], attendance, embedded)

    # Curriculum progress uses the same lesson ID as /content.
    live_lesson_id = str(target["id"]) if embedded else f"live:{target['id']}"
    curriculum_lesson_ids = (
        {str(l.get("id")) for s in (owner.sections or []) for l in (s.get("lessons") or [])} if embedded else set()
    )
    if live_lesson_id in curriculum_lesson_ids:
        # A real lesson: complete it through the shared path so the section, overall percent and certificate
        # eligibility are recomputed too, not just the list of completed lessons.
        _apply_lesson_completion(db, tid, live_lesson_id, participant_email, t=owner)
        return {
            id_field: str(target["id"]),
            "participant_email": participant_email,
            "recorded_at": recorded_at,
            "progress_marked": True,
        }
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
        id_field: str(target["id"]),
        "participant_email": participant_email,
        "recorded_at": recorded_at,
        "progress_marked": True,
    }


def record_live_attendance_service(db: Session, tid: UUID, session_id: str, participant_email: str):
    owner, session, embedded = _resolve_live_attendance_target(db, tid, session_id)
    return _record_attendance_for_target(db, tid, owner, session, embedded, participant_email, id_field="session_id")


def record_lesson_attendance_service(db: Session, tid: UUID, lesson_id: str, participant_email: str):
    owner, lesson, embedded = _resolve_lesson_attendance_target(db, tid, lesson_id)
    return _record_attendance_for_target(db, tid, owner, lesson, embedded, participant_email, id_field="lesson_id")


# ---- Admin-marked per-lesson, per-enrolment attendance roster (any lesson type) ----

def _find_lesson_any_type(training, lesson_id: str):
    """Find a lesson by id across all sections, any type — unlike the self-check-in
    flow above, this manual roster is not restricted to live/venue lessons."""
    from app.services.training_curriculum import normalize_curriculum
    curriculum = normalize_curriculum(training.sections, training.assessments, training.assignments)
    for section in curriculum["sections"]:
        for item in section["lessons"]:
            if str(item.get("id")) == str(lesson_id):
                return section, item
    return None, None


def _lesson_attendance_participant_payload(enrol, record, view: dict | None = None) -> dict:
    marked_by = None
    if record and record.marked_by_id:
        marked_by = {"id": str(record.marked_by_id), "name": record.marked_by_name, "email": record.marked_by_email}
    view = view or {}
    return {
        "enrolment_id": str(enrol.id),
        "participant_name": enrol.participant_name,
        "participant_email": enrol.participant_email,
        "enrolment_status": enrol.status,
        "status": (record.status if record else None) or "not_marked",
        "marked_by": marked_by,
        "marked_at": record.marked_at if record else None,
        # The learner's side of the same lesson: attended completes it, so these follow the attendance.
        "is_completed": view.get("is_completed", False),
        "completed_by_attendance": view.get("completed_by_attendance", False),
        "progress": view.get("progress"),
    }


def get_lesson_attendance_roster_service(db: Session, tid: UUID, lesson_id: str):
    from app.models.training_model import TrainingEnrolment, TrainingLessonAttendance

    training = _get_training_or_404(db, tid)
    _, lesson = _find_lesson_any_type(training, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    enrolments = db.query(TrainingEnrolment).filter(
        TrainingEnrolment.training_id == tid,
        TrainingEnrolment.status.in_(ACTIVE_ENROLMENT_STATUSES),
    ).order_by(TrainingEnrolment.participant_name).all()

    records_by_enrolment = {
        r.enrolment_id: r
        for r in db.query(TrainingLessonAttendance).filter(
            TrainingLessonAttendance.training_id == tid,
            TrainingLessonAttendance.lesson_id == str(lesson_id),
        ).all()
    }

    views = _attendance_completion_views(db, tid, training, lesson_id, enrolments)
    return {
        "training_id": str(tid),
        "lesson_id": str(lesson_id),
        "lesson_title": lesson.get("title"),
        "lesson_type": lesson.get("type"),
        "participants": [
            _lesson_attendance_participant_payload(enrol, records_by_enrolment.get(enrol.id), views.get(enrol.id))
            for enrol in enrolments
        ],
    }


def batch_mark_lesson_attendance_service(db: Session, tid: UUID, lesson_id: str, records: list, actor: dict | None):
    from datetime import datetime
    from sqlalchemy.orm.attributes import flag_modified
    from app.models.training_model import TrainingEnrolment, TrainingLessonAttendance

    training = _get_training_or_404(db, tid)
    _, lesson = _find_lesson_any_type(training, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    seen: set[str] = set()
    for item in records:
        eid = str(item.enrolment_id)
        if eid in seen:
            raise HTTPException(status_code=422, detail=f"Duplicate enrolment_id '{eid}' in records")
        seen.add(eid)

    enrolment_ids = [item.enrolment_id for item in records]
    enrolments_by_id = {
        e.id: e
        for e in db.query(TrainingEnrolment).filter(
            TrainingEnrolment.training_id == tid,
            TrainingEnrolment.id.in_(enrolment_ids),
        ).all()
    }
    for item in records:
        enrol = enrolments_by_id.get(item.enrolment_id)
        if not enrol:
            raise HTTPException(status_code=422, detail=f"Enrolment '{item.enrolment_id}' does not belong to this training")
        if enrol.status not in ACTIVE_ENROLMENT_STATUSES:
            raise HTTPException(
                status_code=422,
                detail=f"Enrolment '{item.enrolment_id}' is not an eligible (active) participant — status is '{enrol.status}'",
            )

    existing_by_enrolment = {
        r.enrolment_id: r
        for r in db.query(TrainingLessonAttendance).filter(
            TrainingLessonAttendance.training_id == tid,
            TrainingLessonAttendance.lesson_id == str(lesson_id),
            TrainingLessonAttendance.enrolment_id.in_(enrolment_ids),
        ).all()
    }

    now = datetime.utcnow()
    actor_id, actor_uuid, actor_name, actor_email, actor_role = _actor_identity_fields(actor)
    previous_by_enrolment: dict = {}

    for item in records:
        new_status = None if item.status == "not_marked" else item.status
        record = existing_by_enrolment.get(item.enrolment_id)
        if not record:
            record = TrainingLessonAttendance(training_id=tid, lesson_id=str(lesson_id), enrolment_id=item.enrolment_id, history=[])
            db.add(record)
            existing_by_enrolment[item.enrolment_id] = record

        previous_status = record.status
        previous_by_enrolment[item.enrolment_id] = previous_status
        record.status = new_status
        record.marked_by_id = actor_uuid if new_status is not None else None
        record.marked_by_name = actor_name if new_status is not None else None
        record.marked_by_email = actor_email if new_status is not None else None
        record.marked_at = now if new_status is not None else None

        history = list(record.history or [])
        history.append({
            "action": "cleared" if new_status is None else "marked",
            "previous_status": previous_status,
            "new_status": new_status,
            "actor_id": actor_id,
            "actor_name": actor_name,
            "actor_email": actor_email,
            "actor_role": actor_role,
            "via": "manual",
            "at": now.isoformat(),
        })
        record.history = history
        flag_modified(record, "history")

    db.commit()
    # attended completes the lesson for the learner; reversing it undoes only what attendance created.
    # Re-sending "attended" is safe: an already-complete lesson is left alone.
    for item in records:
        _sync_attendance_completion(
            db, tid, training, lesson, enrolments_by_id[item.enrolment_id],
            None if item.status == "not_marked" else item.status,
            previous_by_enrolment.get(item.enrolment_id),
        )
    return get_lesson_attendance_roster_service(db, tid, lesson_id)


def _actor_identity_fields(actor: dict | None):
    actor = actor or {}
    actor_id, actor_name, actor_email, actor_role = actor.get("id"), actor.get("name"), actor.get("email"), actor.get("role")
    try:
        actor_uuid = UUID(str(actor_id)) if actor_id else None
    except (TypeError, ValueError):
        actor_uuid = None  # dev/test identities may use a non-UUID id — audit log still records actor_id as a string
    return (str(actor_id) if actor_id is not None else None), actor_uuid, actor_name, actor_email, actor_role


def _lesson_qr_enrolment_code(qr_code: str, lesson_id: str | None) -> str:
    """Resolve a lesson-scoped QR while retaining legacy enrolment QR support."""
    if ":" not in qr_code:
        return qr_code
    enrolment_code, encoded_lesson_id = qr_code.split(":", 1)
    if not enrolment_code or not encoded_lesson_id:
        raise HTTPException(status_code=400, detail="Invalid lesson QR code")
    if lesson_id is None or encoded_lesson_id != str(lesson_id):
        raise HTTPException(status_code=400, detail="QR code belongs to a different lesson")
    return enrolment_code


def qr_check_in_lesson_attendance_service(db: Session, tid: UUID, lesson_id: str, qr_code: str, actor: dict | None):
    """Admin scans a participant's enrolment QR at a specific lesson: verify
    enrolment + lesson, then mark Attended in the same TrainingLessonAttendance
    roster the manual batch-mark endpoint uses — a scan and a manual tick
    produce the identical, auditable record. Idempotent: re-scanning an
    already-attended participant returns 'already_attended' with no new write."""
    from datetime import datetime
    from sqlalchemy.orm.attributes import flag_modified
    from app.models.training_model import TrainingEnrolment, TrainingLessonAttendance, TrainingProgress

    if not qr_code:
        raise HTTPException(status_code=400, detail="qr_code is required")

    training = _get_training_or_404(db, tid)
    _, lesson = _find_lesson_any_type(training, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    qr_code = _lesson_qr_enrolment_code(qr_code, lesson_id)

    enrol = db.query(TrainingEnrolment).filter(
        TrainingEnrolment.qr_code == qr_code,
        TrainingEnrolment.training_id == tid,
    ).first()
    if not enrol:
        raise HTTPException(status_code=404, detail="QR code not recognized, or participant is not enrolled in this training")
    if enrol.status in ("cancelled", "rejected"):
        raise HTTPException(status_code=410, detail=f"QR code has been revoked — enrolment is {enrol.status}")
    if enrol.access_expires_at and enrol.access_expires_at < datetime.utcnow():
        raise HTTPException(status_code=410, detail="QR code has expired — enrolment access has ended")
    if enrol.status not in ACTIVE_ENROLMENT_STATUSES:
        raise HTTPException(status_code=422, detail=f"Participant is not an eligible (active) enrolment — status is '{enrol.status}'")

    prog = db.query(TrainingProgress).filter(
        TrainingProgress.training_id == tid,
        TrainingProgress.participant_email == enrol.participant_email,
    ).first()
    completed_lessons = set(prog.lessons_completed or []) if prog else set()
    accessible, reason = _lesson_is_accessible(lesson, completed_lessons, enrol, training)
    if not accessible:
        raise HTTPException(status_code=403, detail=f"Check-in window closed: {reason}")

    record = db.query(TrainingLessonAttendance).filter(
        TrainingLessonAttendance.training_id == tid,
        TrainingLessonAttendance.lesson_id == str(lesson_id),
        TrainingLessonAttendance.enrolment_id == enrol.id,
    ).first()

    if record and record.status == "attended":
        # A repeat scan writes no new attendance record, but makes sure the lesson is complete (a no-op when it
        # already is), so an attendance recorded before lessons completed on attendance gets fixed by a re-scan.
        _sync_attendance_completion(db, tid, training, lesson, enrol, "attended", "attended")
        views = _attendance_completion_views(db, tid, training, lesson_id, [enrol])
        return {
            **_lesson_attendance_participant_payload(enrol, record, views.get(enrol.id)),
            "result": "already_attended",
            "message": f"{enrol.participant_name} is already marked attended for this lesson",
        }

    now = datetime.utcnow()
    actor_id, actor_uuid, actor_name, actor_email, actor_role = _actor_identity_fields(actor)

    if not record:
        record = TrainingLessonAttendance(training_id=tid, lesson_id=str(lesson_id), enrolment_id=enrol.id, history=[])
        db.add(record)

    previous_status = record.status
    record.status = "attended"
    record.marked_by_id = actor_uuid
    record.marked_by_name = actor_name
    record.marked_by_email = actor_email
    record.marked_at = now
    history = list(record.history or [])
    history.append({
        "action": "marked",
        "previous_status": previous_status,
        "new_status": "attended",
        "actor_id": actor_id,
        "actor_name": actor_name,
        "actor_email": actor_email,
        "actor_role": actor_role,
        "via": "qr_scan",
        "at": now.isoformat(),
    })
    record.history = history
    flag_modified(record, "history")
    db.commit()
    db.refresh(record)

    # Attended completes the lesson for the learner, in the same call as the scan.
    _sync_attendance_completion(db, tid, training, lesson, enrol, "attended", previous_status)
    views = _attendance_completion_views(db, tid, training, lesson_id, [enrol])

    return {
        **_lesson_attendance_participant_payload(enrol, record, views.get(enrol.id)),
        "result": "marked",
        "message": f"{enrol.participant_name} marked attended",
    }


def get_certificate_service(db: Session, tid: UUID, participant_email: str):
    from app.models.training_model import TrainingProgress
    prog=db.query(TrainingProgress).filter(TrainingProgress.training_id==tid, TrainingProgress.participant_email==participant_email).first()
    if prog and not prog.certificate_url:
        # The learner may have finished (100% in /content) without a certificate ever being issued: issue it now.
        try:
            issue_earned_certificate(db, tid, participant_email)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Could not issue the earned certificate for training %s", tid)
            db.rollback()
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


def validate_training_qr_service(db: Session, tid: UUID, qr_code: str | None, *, lesson_id: str | None = None):
    from datetime import datetime

    from app.models.training_model import TrainingEnrolment

    if not qr_code:
        raise HTTPException(status_code=400, detail="qr_code is required")

    qr_code = _lesson_qr_enrolment_code(qr_code, lesson_id)

    training = _get_training_or_404(db, tid)
    enrol = db.query(TrainingEnrolment).filter(
        TrainingEnrolment.qr_code == qr_code,
        TrainingEnrolment.training_id == tid,
    ).first()
    if not enrol:
        raise HTTPException(status_code=404, detail="QR code not found for this training")
    if enrol.status in ("cancelled", "rejected"):
        raise HTTPException(status_code=410, detail=f"QR code has been revoked — enrolment is {enrol.status}")
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


def _find_active_session_id(db: Session, training) -> str | None:
    from app.models.training_model import TrainingLiveSession
    from datetime import datetime
    now = datetime.utcnow()
    sessions = db.query(TrainingLiveSession).filter(
        TrainingLiveSession.training_id == training.id,
        TrainingLiveSession.status != "cancelled"
    ).all()
    if sessions:
        try:
            closest = min(sessions, key=lambda s: abs((s.scheduled_at - now).total_seconds()) if s.scheduled_at else float('inf'))
            return str(closest.id)
        except Exception:
            pass

    from app.services.training_curriculum import normalize_curriculum
    curriculum = normalize_curriculum(training.sections, training.assessments, training.assignments)
    for section in curriculum["sections"]:
        if section.get("type") in ("live", "venue"):
            return str(section["id"])
        for item in section.get("lessons", []):
            if item.get("type") in ("live", "venue"):
                return str(item["id"])
    return None

def check_in_enrolment_service(db: Session, tid: UUID, enrolment_id, qr_code: str | None, current_user: dict | None = None):
    from datetime import datetime

    training = _get_training_or_404(db, tid)
    if training.status in ("cancelled", "archived"):
        raise HTTPException(status_code=400, detail=f"Cannot check-in: training is {training.status}")

    enrol = _find_enrolment_by_id_or_qr(db, tid, enrolment_id, qr_code)
    if not enrol:
        raise HTTPException(status_code=404, detail="Enrolment not found")
    if enrol.status in ("cancelled", "rejected"):
        raise HTTPException(status_code=400, detail=f"Cannot check-in: enrolment is {enrol.status}")
    if enrol.checked_in_at:
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

    # enrol.status = "attended"  # DO NOT change overall enrolment status
    enrol.checked_in_at = datetime.utcnow()
    enrol.checked_in_by = current_user.get("id") if current_user else None
    db.commit()
    db.refresh(enrol)

    # Record attendance at the session level
    session_id = _find_active_session_id(db, training)
    if session_id:
        record_live_attendance_service(db, tid, session_id, enrol.participant_email)

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
    if not enrol.checked_in_at:
        raise HTTPException(status_code=400, detail="Cannot undo: enrolment is not checked in")

    # enrol.status = "enrolled"  # DO NOT change overall enrolment status
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
        "restored_to": enrol.status,
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
        can = r.status in CHECKIN_ELIGIBLE_STATUSES and not r.checked_in_at
        if r.checked_in_at:
            reason = "Already checked in"
        elif r.status == "cancelled":
            reason = "Cancelled — cannot check in"
        elif r.status == "rejected":
            reason = "Rejected — cannot check in"
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
        if enrol.status in ("cancelled", "rejected"):
            results.append({
                "enrolment_id": enrol.id,
                "participant_name": enrol.participant_name,
                "participant_email": enrol.participant_email,
                "status": "failed",
                "message": f"Enrolment is {enrol.status}",
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
        # enrol.status = "attended"  # DO NOT change overall enrolment status
        enrol.checked_in_at = datetime.utcnow()
        enrol.checked_in_by = current_user.get("id") if current_user else None
        
        session_id = _find_active_session_id(db, training)
        if session_id:
            record_live_attendance_service(db, tid, session_id, enrol.participant_email)

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


def get_training_summary_service(db: Session, current_user: dict | None = None, *, access_token: str | None = None):
    from datetime import datetime
    from sqlalchemy import func, or_
    from app.core.auth_context import resolve_auth_tenant_id_with_db
    from app.models.training_model import Training, TrainingEnrolment, TrainingReview

    q = db.query(Training).filter(Training.is_deleted.is_(False))
    role = (current_user or {}).get("role")
    if role != "super_admin":
        tenant_id = resolve_auth_tenant_id_with_db(db, current_user, access_token=access_token)
        if not tenant_id:
            raise HTTPException(status_code=403, detail="Not authorized for this tenant")
        try:
            tenant_id = UUID(str(tenant_id))
        except ValueError:
            raise HTTPException(status_code=403, detail="Not authorized for this tenant")
        # Legacy trainings may predate the denormalized Training.tenant_id column —
        # fall back to the owning Enterprise's tenant_id, same as require_training_owner.
        q = q.outerjoin(Enterprise, Training.enterprise_id == Enterprise.id).filter(
            or_(Training.tenant_id == tenant_id, Enterprise.tenant_id == tenant_id)
        )

    status_rows = q.with_entities(Training.status, func.count(Training.id)).group_by(Training.status).all()
    by_status = {r[0]: r[1] for r in status_rows}
    cat_rows = q.with_entities(Training.category, func.count(Training.id)).group_by(Training.category).all()
    by_category = {r[0]: r[1] for r in cat_rows if r[0]}
    del_rows = q.with_entities(Training.delivery_mode, func.count(Training.id)).group_by(Training.delivery_mode).all()
    by_delivery = {r[0]: r[1] for r in del_rows if r[0]}
    total = sum(by_status.values())

    now = datetime.utcnow()
    upcoming = q.filter(Training.start_date.isnot(None), Training.start_date >= now).count()
    past = q.filter(or_(Training.status == "completed", (Training.end_date.isnot(None)) & (Training.end_date < now))).count()

    tids = [t.id for t in q.with_entities(Training.id).all()]
    total_registrations = 0
    total_attended = 0
    average_rating = None
    if tids:
        total_registrations = (
            db.query(func.count(TrainingEnrolment.id))
            .filter(TrainingEnrolment.training_id.in_(tids), TrainingEnrolment.status.in_(ENROLMENT_CAPACITY_STATUSES))
            .scalar() or 0
        )
        total_attended = (
            db.query(func.count(TrainingEnrolment.id))
            .filter(TrainingEnrolment.training_id.in_(tids), TrainingEnrolment.status == "attended")
            .scalar() or 0
        )
        average_rating_value = review_average(
            parse_rating(r) for (r,) in db.query(TrainingReview.rating).filter(
                TrainingReview.training_id.in_(tids), TrainingReview.moderation_status == "approved"
            ).all()
        )
        if average_rating_value is not None:
            average_rating = average_rating_value

    return {
        "total_trainings": total,
        "upcoming_trainings": upcoming,
        "past_trainings": past,
        "total_registrations": total_registrations,
        "total_enrolments": total_registrations,
        "total_attended": total_attended,
        "average_rating": average_rating,
        "by_status": by_status,
        "by_category": by_category,
        "by_delivery_mode": by_delivery,
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
    try:
        from app.services import training_notifications
        training_notifications.notify_announcement(db, training, entry)
    except Exception:
        pass
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


def create_training_enrol_service(db: Session, tid: UUID, payload: dict, coupon_code: str | None = None, current_user: dict | None = None, *, commit: bool = True, access_token: str | None = None):
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
                    wl = join_waitlist_service(db, tid, {"participant_email": participant_email, "participant_name": participant_name}, current_user, access_token)
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
    # The learner's application user id - saved whenever the caller is the person being enrolled, judged on
    # their verified identity (token claim, then the Auth profile) and not on a bare email-claim match. An
    # admin enrolling someone else must not get their own id stamped on that learner's enrolment.
    from app.services.training_learner_identity import resolve_enrolling_user_id
    e = TrainingEnrolment(training_id=tid, participant_name=participant_name, participant_email=participant_email, user_id=resolve_enrolling_user_id(current_user, participant_email, access_token), group_enrol=payload.get("group_enrol", False), status=status, coupon_code=coupon_code, access_expires_at=expires, qr_code=str(_uuid.uuid4())[:12].upper())
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
    try:
        from app.services import training_notifications, training_workflow_notifications
        if status == "pending_approval":
            training_notifications.notify_enrolment_pending(t, e)
        else:
            training_notifications.notify_enrolment_confirmed(t, e)
            # automatic acceptance — the Enterprise Admin is told once the enrollment is confirmed
            # (held back for a paid training until its payment is recorded)
            training_workflow_notifications.notify_enrollment_confirmed_to_admins(t, e, access_token=access_token)
    except Exception:
        pass
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
            "rejection_reason": r.rejection_reason,
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


def cancel_training_enrol_service(db: Session, tid: UUID, enrol_id: UUID, participant_email: str | None = None, access_token: str | None = None):
    from app.models.training_model import TrainingEnrolment
    q=db.query(TrainingEnrolment).filter(TrainingEnrolment.id==enrol_id, TrainingEnrolment.training_id==tid)
    if participant_email: q=q.filter(TrainingEnrolment.participant_email==participant_email)
    e=q.first()
    if not e: raise HTTPException(404, "Enrolment not found")
    if e.status=="cancelled": return e
    e.status = "cancelled"
    db.commit()
    db.refresh(e)
    promoted = _promote_waitlist(db, tid, access_token)
    try:
        from app.services import training_notifications
        training_notifications.notify_enrolment_cancelled(_get_training_or_404(db, tid), e)
    except Exception:
        pass
    result = {"id": str(e.id), "status": e.status}
    if promoted:
        result["waitlist_promoted"] = promoted
    return result

def approve_training_enrol_service(db: Session, tid: UUID, enrol_id: UUID, action: str, reason: str | None = None, current_user: dict | None = None, access_token: str | None = None):
    from app.models.training_model import TrainingEnrolment
    training = _get_training_or_404(db, tid)
    e = db.query(TrainingEnrolment).filter(TrainingEnrolment.id == enrol_id, TrainingEnrolment.training_id == tid).first()
    if not e:
        raise HTTPException(status_code=404, detail="Enrolment not found")
    already_enrolled = e.status == "enrolled"
    already_rejected = e.status == "rejected"
    if action == "approve":
        e.status = "enrolled"
        e.rejection_reason = None
        _append_moderation(db, training, "enrolment_approved", reason, current_user)
    elif action == "reject":
        # Distinct from self-service "cancelled" — a rejection is admin-initiated
        # and carries a reason the participant can see (mobile: content API and
        # /my/enrolments both surface this via enrolment_status + rejection_reason).
        e.status = "rejected"
        e.rejection_reason = reason
        _append_moderation(db, training, "enrolment_rejected", reason, current_user)
    else:
        raise HTTPException(status_code=400, detail="action must be approve|reject")
    db.commit()
    db.refresh(e)
    db.refresh(training)
    try:
        from app.services import training_notifications, training_workflow_notifications
        from app.services.training_learner_identity import resolve_learner_user_id
        # An enrolment saved before user ids were recorded has none: recover the learner's application user id
        # (and keep it on the row) so accepted / rejected reaches their feed, not just their email.
        resolve_learner_user_id(db, e, training, access_token)
        # Only a real status change notifies: re-approving an enrolled learner or re-rejecting a
        # rejected one is a retry, not a decision. history length identifies this decision.
        decision_index = len(training.moderation_history or [])
        if action == "approve":
            if not already_enrolled:
                training_notifications.notify_enrollment_accepted(training, e, decision_index=decision_index)
                training_workflow_notifications.notify_enrollment_confirmed_to_admins(
                    training, e, actor_id=(current_user or {}).get("id"), access_token=access_token,
                )
        elif not already_rejected:
            training_notifications.notify_enrollment_rejected(training, e, reason, decision_index=decision_index)
    except Exception:
        pass
    return {"id": str(e.id), "status": e.status, "reason": reason}

def create_training_checkout_service(db: Session, tid: UUID, payload, current_user: dict | None = None, access_token: str | None = None):
    from app.models.training_model import TrainingOrder
    data = payload if isinstance(payload, dict) else payload.model_dump()
    t = _get_training_or_404(db, tid)
    coupon = data.get("coupon_code")
    try:
        # Reuse enrolment rules without committing; order and enrolment are atomic.
        enrol = create_training_enrol_service(db, tid, {
            "participant_name": data.get("participant_name"),
            "participant_email": data.get("participant_email"),
        }, coupon_code=coupon, current_user=current_user, commit=False, access_token=access_token)
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
    if enrol.status == "enrolled":  # the order is committed with a confirmed payment — safe to tell the admin now
        try:
            from app.services import training_workflow_notifications
            training_workflow_notifications.notify_enrollment_confirmed_to_admins(t, enrol, access_token=access_token)
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


def publish_training_service(db: Session, tid: UUID, current_user: dict | None = None, access_token: str | None = None):
    return update_training_status_service(db, tid, "published", current_user, access_token=access_token)


def unpublish_training_service(db: Session, tid: UUID, current_user: dict | None = None, access_token: str | None = None):
    return update_training_status_service(db, tid, "unpublished", current_user, access_token=access_token)


def suspend_training_service(db: Session, tid: UUID, reason: str | None = None, current_user: dict | None = None, access_token: str | None = None):
    return update_training_status_service(db, tid, "suspended", current_user, notes=reason, access_token=access_token)


def cancel_training_service(db: Session, tid: UUID, reason: str | None = None, current_user: dict | None = None, access_token: str | None = None):
    return update_training_status_service(db, tid, "cancelled", current_user, notes=reason, access_token=access_token)


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


def reorder_sections_service(db: Session, tid: UUID, payload):
    from sqlalchemy.orm.attributes import flag_modified
    training = _get_training_or_404(db, tid)
    sections = training.sections or []
    mapping = {str(s.get("id")): s for s in sections if s.get("id")}

    order_by_id: dict[str, int] = {}
    for entry in payload.section_orders:
        sid = str(entry.id)
        if sid not in mapping:
            raise HTTPException(status_code=422, detail=f"Section '{sid}' does not belong to this training")
        if sid in order_by_id:
            raise HTTPException(status_code=422, detail=f"Duplicate section id '{sid}' in section_orders")
        order_by_id[sid] = entry.order

    ordered_ids = sorted(order_by_id, key=lambda sid: order_by_id[sid])
    remaining_ids = [str(s.get("id")) for s in sections if str(s.get("id")) not in order_by_id]
    training.sections = [mapping[i] for i in ordered_ids] + [mapping[i] for i in remaining_ids]
    flag_modified(training, "sections")
    db.commit()
    db.refresh(training)
    return training.sections


def reorder_lessons_service(db: Session, tid: UUID, section_id: str, payload):
    from sqlalchemy.orm.attributes import flag_modified
    training = _get_training_or_404(db, tid)
    section = next((s for s in (training.sections or []) if str(s.get("id")) == str(section_id)), None)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    lessons = section.get("lessons") or section.get("items") or []
    mapping = {str(l.get("id")): l for l in lessons if l.get("id")}

    if payload.lesson_orders:
        order_by_id: dict[str, int] = {}
        for entry in payload.lesson_orders:
            lid = str(entry.id)
            if lid not in mapping:
                raise HTTPException(status_code=422, detail=f"Lesson '{lid}' does not belong to this section")
            if lid in order_by_id:
                raise HTTPException(status_code=422, detail=f"Duplicate lesson id '{lid}' in lesson_orders")
            order_by_id[lid] = entry.order
        ordered_ids = sorted(order_by_id, key=lambda lid: order_by_id[lid])
        seen = set(order_by_id)
    else:
        seen = set()
        ordered_ids = []
        for lid in payload.order or []:
            lid = str(lid)
            if lid not in mapping:
                raise HTTPException(status_code=422, detail=f"Lesson '{lid}' does not belong to this section")
            if lid in seen:
                raise HTTPException(status_code=422, detail=f"Duplicate lesson id '{lid}' in order")
            seen.add(lid)
            ordered_ids.append(lid)

    remaining_ids = [str(l.get("id")) for l in lessons if str(l.get("id")) not in seen]
    reordered = [mapping[i] for i in ordered_ids] + [mapping[i] for i in remaining_ids]
    section["lessons"] = reordered
    if "items" in section:
        from copy import deepcopy
        section["items"] = deepcopy(reordered)
    flag_modified(training, "sections")
    db.commit()
    db.refresh(training)
    return section["lessons"]


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


def _format_scheduled_at(value, time_zone: str | None) -> str | None:
    """Formats a lesson's scheduled_at with an explicit UTC offset. A naive
    stored value is interpreted as wall-clock time in the training's
    time_zone — same convention as Training.enrolment_start/enrolment_end."""
    from datetime import datetime, timezone as _timezone

    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return value
    else:
        return None
    if dt.tzinfo is not None:
        return dt.isoformat()

    import zoneinfo

    tz_str = time_zone or "UTC"
    try:
        tz = _timezone.utc if tz_str == "UTC" else zoneinfo.ZoneInfo(tz_str)
    except Exception:
        tz = _timezone.utc
    return dt.replace(tzinfo=tz).isoformat()


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


def _base_lesson_payload(lesson: dict, is_locked: bool, is_completed: bool, completed_at: str | None, *, attended_at: str | None = None, time_zone: str | None = None) -> dict:
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
        "scheduled_at": _format_scheduled_at(lesson.get("scheduled_at"), time_zone),
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
    if ltype in ("live", "venue"):
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
        any_enrol = _latest_enrolment_any_status(db, tid, email) if email else None
        if any_enrol and any_enrol.status == "pending_approval":
            raise HTTPException(status_code=403, detail={
                "code": "ENROLMENT_PENDING_APPROVAL",
                "message": "Admin has not approved your enrolment yet.",
            })
        if any_enrol and any_enrol.status in ("rejected", "cancelled"):
            raise HTTPException(status_code=403, detail={
                "code": "ENROLMENT_NOT_ACTIVE",
                "message": "Your enrolment is not active.",
                "enrolment_status": any_enrol.status,
                "rejection_reason": any_enrol.rejection_reason,
            })
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
    position_by_lesson_id: dict = {}
    resume_section_id = None
    resume_lesson_id = None
    if email:
        prog = db.query(TrainingProgress).filter(
            TrainingProgress.training_id == tid,
            TrainingProgress.participant_email == email,
        ).first()
        if prog:
            completed_lesson_ids = set(prog.lessons_completed or [])
            position_by_lesson_id = prog.lesson_positions or {}
            resume_section_id, resume_lesson_id = _resume_lesson_from_progress(training, prog)

    attended_at_by_lesson_id: dict[str, str | None] = {}
    if email:
        for _sec in (training.sections or []):
            if _sec.get("type") in ("live", "venue"):
                for _rec in _live_attendance_rows(_sec.get("attendance")):
                    if _rec.get("participant_email") == email:
                        attended_at_by_lesson_id[str(_sec.get("id"))] = _rec.get("recorded_at")
            for _item in (_sec.get("lessons") or _sec.get("items") or []):
                if _item.get("type") in ("live", "venue"):
                    for _rec in _live_attendance_rows(_item.get("attendance")):
                        if _rec.get("participant_email") == email:
                            attended_at_by_lesson_id[str(_item.get("id"))] = _rec.get("recorded_at")

    # Admin scans/manual roster marks are authoritative over legacy self-check-in.
    if enrol:
        from app.models.training_model import TrainingLessonAttendance
        for record in db.query(TrainingLessonAttendance).filter(
            TrainingLessonAttendance.training_id == tid,
            TrainingLessonAttendance.enrolment_id == enrol.id,
        ).all():
            attended_at_by_lesson_id[str(record.lesson_id)] = (
                record.marked_at.isoformat()
                if record.status == "attended" and record.marked_at else None
            )

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
                lesson_payload = _base_lesson_payload(lesson, locked, is_completed, completed_at, time_zone=training.time_zone)
                lesson_payload["assessment"] = (
                    _build_exam_assessment_payload(assessment, submission, include_answer=is_staff)
                    if assessment and not locked
                    else None
                )
            else:
                if is_staff:
                    locked = False
                is_completed = str(lesson.get("id")) in completed_lesson_ids
                attended_at = attended_at_by_lesson_id.get(str(lesson.get("id"))) if ltype in ("live", "venue") else None
                lesson_payload = _base_lesson_payload(lesson, locked, is_completed, None, attended_at=attended_at, time_zone=training.time_zone)
            position = position_by_lesson_id.get(str(lesson.get("id")))
            lesson_payload["progress_seconds"] = position.get("position_seconds") if position else None
            lesson_payload["duration_seconds"] = position.get("duration_seconds") if position else None
            lesson_payload["last_accessed_at"] = position.get("last_accessed_at") if position else None
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
        if sec_type in ("live", "venue"):
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
        "resume_section_id": resume_section_id,
        "resume_lesson_id": resume_lesson_id,
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
        if current_user.get("role") in ("admin", "provider", "super_admin"):
            try:
                from app.services import training_notifications
                training_notifications.notify_question_answered(
                    training, discussion=entry, answer_text=reply["text"], answered_by=reply["author"],
                )
            except Exception:
                pass
        return entry
    raise HTTPException(status_code=404, detail="Discussion not found")


def _utc_iso(value) -> str | None:
    """A stored timestamp as ISO 8601 UTC with a trailing Z. History entries are written with
    datetime.utcnow().isoformat() — UTC, but with no designator — so naive values are UTC by definition;
    an offset-aware value is converted."""
    from datetime import datetime, timezone

    if not value:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    parsed = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    return parsed.isoformat().replace("+00:00", "Z")


def get_moderation_history_service(db: Session, tid: UUID):
    """Every history entry with its own `created_at` (UTC, ISO 8601, trailing Z), oldest first. Existing
    keys are returned unchanged — `at` is the same instant without the Z."""
    training = _get_training_or_404(db, tid)
    out = []
    for entry in getattr(training, "moderation_history", None) or []:
        item = dict(entry) if isinstance(entry, dict) else {"action": str(entry)}
        item["created_at"] = _utc_iso(item.get("created_at") or item.get("at"))
        out.append(item)
    return out


# ---- Reviews (verified — must be enrolled; moderated: pending -> approved | rejected) ----

def _approved_review_stats(db: Session, tids: list) -> dict:
    """{training_id: (average or None, approved count)} for a page of trainings, in one query."""
    from app.models.training_model import TrainingReview

    if not tids:
        return {}
    by_training: dict = {}
    for training_id, rating in db.query(TrainingReview.training_id, TrainingReview.rating).filter(
        TrainingReview.training_id.in_(tids), TrainingReview.moderation_status == "approved"
    ).all():
        value = parse_rating(rating)
        if value is not None:
            by_training.setdefault(training_id, []).append(value)
    return {tid: (average_rating(ratings), len(ratings)) for tid, ratings in by_training.items()}


def _reviewer_names(db: Session, tid: UUID, emails: set) -> dict[str, str]:
    """{email: name on the learner's enrolment}. An email is never used as a name."""
    from app.models.training_model import TrainingEnrolment

    emails = {e for e in emails if e}
    names: dict[str, str] = {}
    if not emails:
        return names
    for enrolment in db.query(TrainingEnrolment).filter(
        TrainingEnrolment.training_id == tid,
        TrainingEnrolment.participant_email.in_(emails),
    ).order_by(TrainingEnrolment.created_at.desc()).all():
        shown = public_name(enrolment.participant_name)
        if shown and enrolment.participant_email not in names:
            names[enrolment.participant_email] = shown
    return names


def _review_dict(r, *, name: str | None, include_email: bool) -> dict:
    return {
        "id": str(r.id), "training_id": str(r.training_id), "rating": parse_rating(r.rating),
        "comment": r.comment, "participant_email": r.participant_email if include_email else None,
        "participant_name": name, "verified": True,
        "moderation_status": r.moderation_status or "pending",
        "created_at": r.created_at.isoformat(),
        "updated_at": (r.updated_at or r.created_at).isoformat(),
    }


def create_training_review_service(db: Session, tid: UUID, data, *, access_token: str | None = None):
    from app.models.training_model import TrainingEnrolment, TrainingReview
    from app.services import review_notifications

    _get_training_or_404(db, tid)
    comment = clean_comment(data.comment)
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
    is_new = existing is None
    if existing:
        # Editing keeps the status the review already has.
        existing.rating = str(data.rating)
        existing.comment = comment
        db.commit()
        db.refresh(existing)
        r = existing
    else:
        r = TrainingReview(
            training_id=tid, participant_email=data.participant_email, rating=str(data.rating),
            comment=comment, moderation_status="pending",
        )
        db.add(r)
        try:
            db.commit()
        except IntegrityError:
            # Two first posts from the same learner at the same moment: update the one that won.
            db.rollback()
            r = db.query(TrainingReview).filter(
                TrainingReview.training_id == tid, TrainingReview.participant_email == data.participant_email
            ).first()
            if r is None:
                raise
            r.rating = str(data.rating)
            r.comment = comment
            db.commit()
            is_new = False
        db.refresh(r)

    if is_new:
        review_notifications.notify_review_submitted("training", r.id, access_token=access_token)
    return _review_dict(r, name=public_name(enrolled.participant_name), include_email=True)


def list_training_reviews_service(db: Session, tid: UUID, opts=None):
    """Public: approved reviews only, no email addresses. `opts` (sort / rating / with_comment / paging) only
    changes which reviews are listed; the average, count and star breakdown cover all approved reviews."""
    from app.models.training_model import TrainingReview

    _get_training_or_404(db, tid)
    rows = [
        r for r in db.query(TrainingReview).filter(
            TrainingReview.training_id == tid, TrainingReview.moderation_status == "approved"
        ).order_by(TrainingReview.created_at.desc()).all()
        if parse_rating(r.rating) is not None
    ]
    ratings = [parse_rating(r.rating) for r in rows]
    shown, pagination = apply_list_options(
        rows, opts or ListOptions(),
        rating_of=lambda r: parse_rating(r.rating), created_of=lambda r: r.created_at, comment_of=lambda r: r.comment,
    )
    names = _reviewer_names(db, tid, {r.participant_email for r in shown})
    return {
        "reviews": [_review_dict(r, name=names.get(r.participant_email), include_email=False) for r in shown],
        "average_rating": average_rating(ratings),
        "count": len(rows),
        "rating_distribution": rating_distribution(ratings),
        "pagination": pagination,
    }


def get_my_training_review_service(db: Session, tid: UUID, current_user: dict):
    """The caller's own review of this training, whatever its status; None when they have not written one."""
    from app.models.training_model import TrainingReview

    _get_training_or_404(db, tid)
    email = (current_user or {}).get("email")
    if not email:
        return None
    r = db.query(TrainingReview).filter(
        TrainingReview.training_id == tid, TrainingReview.participant_email == email
    ).first()
    if r is None:
        return None
    return _review_dict(r, name=_reviewer_names(db, tid, {email}).get(email), include_email=True)


def moderate_training_review_service(db: Session, tid: UUID, review_id: UUID, action: str, access, *, access_token: str | None = None, actor: dict | None = None):
    """Approve / reject / reset a review. `access` is the caller's CatalogAccess (Enterprise Admin or Super Admin)."""
    from app.models.training_model import TrainingReview
    from app.services import review_notifications
    from app.services.review_access import assert_can_moderate

    validate_action(action)
    training = _get_training_or_404(db, tid)
    assert_can_moderate(
        access,
        training.enterprise.tenant_id if training.enterprise else None,
        training.tenant_id,
    )
    r = db.query(TrainingReview).filter(TrainingReview.id == review_id, TrainingReview.training_id == tid).first()
    if not r:
        raise HTTPException(status_code=404, detail="Review not found")
    previous = r.moderation_status
    if previous != action:
        review_audit.record(
            db, module="training", review_id=r.id, item_id=training.id, item_name=training.title,
            tenant_id=(training.enterprise.tenant_id if training.enterprise else None) or training.tenant_id,
            from_status=previous, to_status=action, actor=actor, actor_role=access.role,
        )
    r.moderation_status = action
    db.commit()
    db.refresh(r)
    if previous != action:
        review_notifications.notify_review_decision("training", r.id, action, access_token=access_token)
    return _review_dict(r, name=_reviewer_names(db, tid, {r.participant_email}).get(r.participant_email), include_email=True)


def delete_training_review_service(db: Session, tid: UUID, review_id: UUID, current_user: dict, *, access_token: str | None = None):
    """The review's author, or staff (admin / provider) of the business that owns the training, or a Super Admin."""
    from app.models.training_model import TrainingReview
    from app.services.review_access import assert_staff_in_tenant

    training = _get_training_or_404(db, tid)
    r = db.query(TrainingReview).filter(TrainingReview.id == review_id, TrainingReview.training_id == tid).first()
    if not r:
        raise HTTPException(status_code=404, detail="Review not found")
    email = str((current_user or {}).get("email") or "").strip().lower()
    if not email or email != (r.participant_email or "").strip().lower():
        role = str((current_user or {}).get("role") or "").lower()
        if role not in ("admin", "provider", "super_admin"):
            raise HTTPException(status_code=403, detail="You can only delete your own review")
        owners = ((training.enterprise.tenant_id if training.enterprise else None), training.tenant_id)
        assert_staff_in_tenant(db, current_user, access_token, *owners)
        review_audit.record(
            db, module="training", review_id=r.id, item_id=training.id, item_name=training.title,
            tenant_id=next((t for t in owners if t), None), from_status=r.moderation_status, to_status="deleted",
            actor=current_user, actor_role=role,
        )
    db.delete(r)
    db.commit()
    return {"message": "Review deleted"}


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

    average, count = _approved_review_stats(db, [tid]).get(tid, (None, 0))
    return {
        "id": str(item.id), "training_id": str(training.id), "title": training.title,
        "primary_image": training.primary_image, "price": training.price, "currency": training.currency,
        "average_rating": average, "reviews_count": count,
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
    stats = _approved_review_stats(db, [item.training_id for item in rows])
    items = []
    for item in rows:
        training = get_training_by_id(db, item.training_id, include_deleted=True)
        if not training:
            continue
        average, count = stats.get(item.training_id, (None, 0))
        items.append({
            "id": str(item.id), "training_id": str(training.id), "title": training.title,
            "primary_image": training.primary_image, "price": training.price, "currency": training.currency,
            "average_rating": average, "reviews_count": count,
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


def _latest_enrolment_any_status(db: Session, tid: UUID, email: str | None):
    """Unlike _learner_enrolment (active-only), finds the caller's most recent
    enrolment attempt regardless of status — used to report is_enrolled/
    enrolment_status/rejection_reason even when pending, rejected, or cancelled."""
    from app.models.training_model import TrainingEnrolment
    if not email:
        return None
    return db.query(TrainingEnrolment).filter(
        TrainingEnrolment.training_id == tid,
        TrainingEnrolment.participant_email == email,
    ).order_by(TrainingEnrolment.created_at.desc()).first()


def get_learner_training_detail_service(db: Session, tid: UUID, current_user: dict):
    detail = get_training_service(db, tid, current_user).model_dump()
    if current_user.get("role") in ("admin", "provider", "super_admin"):
        return TrainingDetailResponse.model_validate(detail)
    enrolment = _learner_enrolment(db, tid, current_user)
    latest_enrolment = _latest_enrolment_any_status(db, tid, current_user.get("email"))
    from app.services.training_curriculum import curriculum_preview
    detail["sections"] = curriculum_preview(detail.get("sections") or [])
    detail["assessments"] = []
    if not enrolment:
        detail["assignments"] = []
    for field in ("instructor_notes", "last_admin_notes"):
        detail[field] = None
    detail["is_enrolled"] = bool(latest_enrolment) and latest_enrolment.status not in ("cancelled", "rejected", "waitlisted")
    detail["enrolment_status"] = latest_enrolment.status if latest_enrolment else None
    detail["rejection_reason"] = latest_enrolment.rejection_reason if latest_enrolment else None
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


# ---- TrainingCategory CRUD (Super Admin-managed taxonomy, mirrors EventCategory) ----


def create_training_category_service(db: Session, payload):
    from app.models.training_model import TrainingCategory

    name = payload.name.strip()
    existing = db.query(TrainingCategory).filter(TrainingCategory.name == name).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Category '{name}' already exists")
    if payload.parent_id:
        parent = db.query(TrainingCategory).filter(TrainingCategory.id == payload.parent_id).first()
        if not parent:
            raise HTTPException(status_code=404, detail="Parent category not found")
    cat = TrainingCategory(name=name, parent_id=payload.parent_id, description=payload.description)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


def list_training_categories_service(db: Session):
    from app.models.training_model import TrainingCategory
    return db.query(TrainingCategory).order_by(TrainingCategory.name).all()


def update_training_category_service(db: Session, category_id: UUID, payload):
    from app.models.training_model import TrainingCategory

    cat = db.query(TrainingCategory).filter(TrainingCategory.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    if payload.name is not None:
        new_name = payload.name.strip()
        dup = db.query(TrainingCategory).filter(TrainingCategory.name == new_name, TrainingCategory.id != category_id).first()
        if dup:
            raise HTTPException(status_code=400, detail=f"Category '{new_name}' already exists")
        cat.name = new_name
    if payload.description is not None:
        cat.description = payload.description
    db.commit()
    db.refresh(cat)
    return cat


def delete_training_category_service(db: Session, category_id: UUID):
    from app.models.training_model import TrainingCategory

    cat = db.query(TrainingCategory).filter(TrainingCategory.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    child_count = db.query(TrainingCategory).filter(TrainingCategory.parent_id == category_id).count()
    if child_count > 0:
        raise HTTPException(status_code=400, detail=f"Cannot delete: category has {child_count} subcategories. Delete subcategories first.")
    db.delete(cat)
    db.commit()
    return {"message": "Category deleted"}
