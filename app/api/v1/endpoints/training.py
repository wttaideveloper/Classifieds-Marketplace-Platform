from uuid import UUID
from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy.orm import Session
from app.core.dependencies import get_current_user, get_web_session_cookie_token, require_event_form_builder_admin, require_roles
from app.db.database import get_db
from app.services.training_curriculum import save_builder_curriculum
from app.schemas.training_schema import TrainingEnrolmentResponse, TrainingEnrolWaitlistResponse
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.training_schema import AnnouncementCreate, AssessmentCreate, AssessmentQuestionCreate, AssessmentReviewResponse, AssessmentSubmitCreate, AssessmentSubmitResponse, AssignmentCreate, AssignmentSubmitCreate, AssignmentSubmitResponse, LessonCreate, TrainingAssignmentResponse, SectionCreate, TopicCreate, TrainingBatchCheckInRequest, TrainingBatchCheckInResponse, TrainingCheckInPreviewItem, TrainingCheckInRequest, TrainingCompleteLessonRequest, TrainingCompleteLessonResponse, TrainingCreate, TrainingDetailResponse, TrainingEnrolCheckInRequest, TrainingEnrolCheckInResponse, TrainingEnrolUncheckInRequest, TrainingEnrolUncheckInResponse, TrainingLiveSessionCreate, TrainingPaginatedResponse, TrainingResponse, TrainingReviewCreate, TrainingReviewListResponse, TrainingReviewResponse, TrainingStatusUpdate, TrainingUpdate, TrainingValidateQrRequest, TrainingValidateQrResponse, TrainingWishlistItemResponse
from app.services.training_service import add_assessment_question_service, check_in_training_service, complete_lesson_service, create_assignment_service, create_live_session_service, create_training_announcement_service, create_training_service, delete_training_service, delete_training_assignment_service, duplicate_training_service, get_certificate_service, get_live_sessions_service, get_training_admin_notes_service, get_training_progress_service, get_training_service, get_trainings_service, grade_assignment_service, record_live_attendance_service, restore_training_service, submit_assessment_service, submit_assignment_service, update_training_service, update_training_status_service, publish_training_service, unpublish_training_service, suspend_training_service, cancel_training_service, delete_section_service, get_lesson_service, list_lesson_topics_service, add_lesson_topic_service, update_lesson_topic_service, delete_lesson_topic_service, update_assessment_service, delete_assessment_service, delete_assessment_question_service, filter_assessments, get_secure_training_content_service, reply_discussion_service, get_moderation_history_service, list_training_announcements_service, get_live_attendance_service, export_live_attendance_service, approve_training_enrol_service, list_training_assignments_service
from app.services.training_service import (
    add_training_wishlist_service,
    create_training_review_service,
    generate_training_notes_pdf_service,
    get_lesson_download_service,
    list_downloadable_lessons_service,
    list_training_reviews_service,
    list_training_wishlist_service,
    remove_training_wishlist_service,
)

router = APIRouter(tags=["Trainings"])


def _auth_token_from_request(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1]
    return get_web_session_cookie_token(request)


def require_training_manager(request: Request, training_id: UUID, db: Session = Depends(get_db),
                             current_user: dict = Depends(require_roles(["admin", "provider", "super_admin"]))):
    from app.repository.training_repo import require_training_owner
    token = _auth_token_from_request(request)
    training = require_training_owner(db, training_id, current_user, access_token=token, include_deleted=True)
    return {**current_user, "tenant_id": str(training.tenant_id or (training.enterprise.tenant_id if training.enterprise else ""))}


def _require_tenant_ownership_if_staff(request: Request, training_id: UUID, db: Session, current_user: dict):
    """Staff (admin/provider/super_admin) acting on someone else's enrolment/waitlist
    entry must own the training's tenant — same check require_training_manager applies
    to the curriculum endpoints. Learners are left untouched; they're scoped to their
    own participant_email by the caller instead."""
    if not current_user or current_user.get("role") not in ("admin", "provider", "super_admin"):
        return
    from app.repository.training_repo import require_training_owner
    token = _auth_token_from_request(request)
    require_training_owner(db, training_id, current_user, access_token=token)


from fastapi import File, Form, UploadFile
from fastapi.responses import FileResponse as FastAPIFileResponse
from app.schemas.training_schema import TrainingUploadPurpose, TrainingUploadResponse
from app.services.training_upload_service import save_training_upload, resolve_training_upload


@router.post(
    "/upload",
    response_model=TrainingUploadResponse,
    status_code=201,
    summary="Upload training media (lesson videos, PDFs, documents)",
    description=(
        "Upload a lesson video, PDF, or document. Validates file type, size, and "
        "purpose. Returns a publicly-served URL under /api/v1/trainings/upload/."
    ),
)
def upload_training_media(
    file: UploadFile = File(..., description="File to upload."),
    purpose: TrainingUploadPurpose | None = Form(
        None,
        description="lesson_video | lesson_pdf | lesson_document | audio | image (inferred from content type when omitted)",
    ),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_roles(["admin", "provider"])),
):
    file_bytes = file.file.read() if file.file else b""
    return save_training_upload(
        file_bytes,
        file.filename or "upload",
        file.content_type,
        purpose.value if purpose else None,
        db,
        current_user,
    )


@router.get(
    "/upload/{stored_name}",
    summary="Serve uploaded training media file",
    description="Streams a previously uploaded training media file by its stored name.",
)
def download_training_media(
    stored_name: str = Path(..., description="Stored file name returned by the upload endpoint."),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    path = resolve_training_upload(stored_name)
    return FastAPIFileResponse(path=str(path))

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

@router.get("/reports/summary", summary="Training portfolio summary")
def training_summary(enterprise_id: UUID | None = None, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.services.training_service import get_training_summary_service
    return get_training_summary_service(db, enterprise_id)

@router.get(
    "/form-configuration/active",
    summary="Resolved active Training form for authenticated Enterprise Admin",
    description=(
        "Authenticate → resolve tenant → active selective assignment → else active global. "
        "Returns 404 when none (frontend uses static Create Training wizard fallback)."
    ),
)
def get_active_training_form_configuration(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_event_form_builder_admin),
):
    from app.schemas.training_form_config_schema import ActiveFormConfigurationResponse
    from app.services.training_form_config_service import get_active_form_configuration_service

    access_token = None
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        access_token = auth_header.split(" ", 1)[1].strip()
    if not access_token:
        access_token = get_web_session_cookie_token(request)

    return ActiveFormConfigurationResponse.model_validate(
        get_active_form_configuration_service(db, current_user, access_token=access_token)
    )


@router.get(
    "/{training_id}/form-configuration",
    summary="Historical Training form version used by this Training",
)
def get_training_form_configuration(
    training_id: UUID = Path(..., description="Training ID"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_training_manager),
):
    from app.schemas.training_form_config_schema import ActiveFormConfigurationResponse
    from app.services.training_form_config_service import get_training_form_configuration_service
    return ActiveFormConfigurationResponse.model_validate(get_training_form_configuration_service(db, training_id, current_user))


@router.get("/{training_id}", response_model=TrainingDetailResponse, summary="Get training detail")
def get_training(training_id: UUID = Path(...), db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services.training_service import get_learner_training_detail_service
    return get_learner_training_detail_service(db, training_id, current_user)

@router.put("/{training_id}", response_model=TrainingResponse)
def update_training(data: TrainingUpdate, training_id: UUID = Path(...), db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    return update_training_service(db, training_id, data, current_user)

@router.delete("/{training_id}")
def delete_training(training_id: UUID = Path(...), db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    delete_training_service(db, training_id); return {"message":"Training deleted"}

@router.post("/{training_id}/duplicate", response_model=TrainingResponse, status_code=201)
def duplicate(training_id: UUID = Path(...), db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    return duplicate_training_service(db, training_id)

@router.patch("/{training_id}/status", response_model=TrainingResponse, summary="Update training status (generic transition)")
def update_status(training_id: UUID, payload: TrainingStatusUpdate, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    if payload.status in ("approved", "rejected", "needs_revision") and current_user.get("role") not in ("admin", "super_admin"):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Only Super Admin can approve/reject/request-changes")
    return update_training_status_service(db, training_id, payload.status, current_user, notes=payload.reason)


@router.post("/{training_id}/publish", response_model=TrainingResponse, status_code=200, summary="Publish training")
def publish_training(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    return publish_training_service(db, training_id, current_user)


@router.post("/{training_id}/unpublish", response_model=TrainingResponse, status_code=200, summary="Unpublish training (sets status=unpublished)")
def unpublish_training(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    return unpublish_training_service(db, training_id, current_user)


@router.post("/{training_id}/suspend", response_model=TrainingResponse, status_code=200, summary="Suspend published training")
def suspend_training(training_id: UUID, payload: TrainingStatusUpdate | None = None, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    reason = payload.reason if payload else None
    return suspend_training_service(db, training_id, reason=reason, current_user=current_user)


@router.post("/{training_id}/cancel", response_model=TrainingResponse, status_code=200, summary="Cancel training")
def cancel_training(training_id: UUID, payload: TrainingStatusUpdate | None = None, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    reason = payload.reason if payload else None
    return cancel_training_service(db, training_id, reason=reason, current_user=current_user)


@router.post("/{training_id}/archive", response_model=TrainingResponse, status_code=200, summary="Archive training")
def archive_training(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    return update_training_status_service(db, training_id, "archived", current_user)


@router.post(
    "/{training_id}/restore",
    response_model=TrainingResponse,
    status_code=200,
    summary="Restore archived training to draft",
    description="Transitions archived → draft and clears is_deleted. Equivalent to PATCH status with {\"status\":\"draft\"}.",
)
def restore_training(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    return restore_training_service(db, training_id, current_user)

@router.get("/{training_id}/moderation-history", summary="Admin moderation / rejection history")
def moderation_history(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    return get_moderation_history_service(db, training_id)

# Builder - sections / lessons (T7) - stored as JSONB on training
@router.get("/{training_id}/sections")
def list_sections(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    t = get_training_service(db, training_id); return t.sections or []

@router.post("/{training_id}/sections", status_code=201)
def add_section(training_id: UUID, payload: SectionCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    from app.repository.training_repo import get_training_by_id
    import uuid
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    secs = list(obj.sections or [])
    new = {"id": str(uuid.uuid4()), **payload.model_dump(mode="json")}
    secs.append(new)
    obj.sections = secs
    return save_builder_curriculum(db, obj, new["id"])

@router.put("/{training_id}/sections/{section_id}")
def update_section(training_id: UUID, section_id: str, payload: SectionCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    from app.repository.training_repo import get_training_by_id
    from sqlalchemy.orm.attributes import flag_modified
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    for s in obj.sections or []:
        if s.get("id")==section_id:
            changes = payload.model_dump(exclude_unset=True, mode="json")
            if "items" in changes and "lessons" not in changes:
                s.pop("lessons", None)
            s.update(changes)
            return save_builder_curriculum(db, obj, section_id)
    from fastapi import HTTPException; raise HTTPException(404, "Section not found")

@router.delete("/{training_id}/sections/{section_id}", summary="Delete section/module")
def delete_section(training_id: UUID, section_id: str, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    return delete_section_service(db, training_id, section_id)

@router.get("/{training_id}/sections/{section_id}/lessons/{lesson_id}", summary="Get single lesson")
def get_lesson(training_id: UUID, section_id: str, lesson_id: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return get_lesson_service(db, training_id, section_id, lesson_id, current_user)

@router.get("/{training_id}/sections/{section_id}/lessons/{lesson_id}/topics", summary="List lesson topics")
def list_topics(training_id: UUID, section_id: str, lesson_id: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return list_lesson_topics_service(db, training_id, section_id, lesson_id)

@router.post("/{training_id}/sections/{section_id}/lessons/{lesson_id}/topics", status_code=201, summary="Add lesson topic")
def add_topic(training_id: UUID, section_id: str, lesson_id: str, payload: TopicCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    return add_lesson_topic_service(db, training_id, section_id, lesson_id, payload)

@router.put("/{training_id}/sections/{section_id}/lessons/{lesson_id}/topics/{topic_id}", summary="Update lesson topic")
def update_topic(training_id: UUID, section_id: str, lesson_id: str, topic_id: str, payload: TopicCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    return update_lesson_topic_service(db, training_id, section_id, lesson_id, topic_id, payload)

@router.delete("/{training_id}/sections/{section_id}/lessons/{lesson_id}/topics/{topic_id}", summary="Delete lesson topic")
def delete_topic(training_id: UUID, section_id: str, lesson_id: str, topic_id: str, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    return delete_lesson_topic_service(db, training_id, section_id, lesson_id, topic_id)

@router.post("/{training_id}/sections/reorder", summary="Reorder sections/modules")
def reorder_sections(training_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
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
def reorder_modules(training_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
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
def reorder_lessons(training_id: UUID, section_id: str, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
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
def add_lesson(training_id: UUID, section_id: str, payload: LessonCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
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
            lessons = s.get("lessons", [])
            new = {"id": str(uuid.uuid4()), **payload.model_dump(mode="json")}
            lessons.append(new)
            s["lessons"] = lessons
            return save_builder_curriculum(db, obj, section_id, new["id"])
    from fastapi import HTTPException; raise HTTPException(404, "Section not found")

@router.put("/{training_id}/sections/{section_id}/lessons/{lesson_id}")
def update_lesson(training_id: UUID, section_id: str, lesson_id: str, payload: LessonCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    from app.repository.training_repo import get_training_by_id
    from sqlalchemy.orm.attributes import flag_modified
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    for s in obj.sections or []:
        if s.get("id")==section_id:
            for ls in s.get("lessons", []):
                if ls.get("id")==lesson_id:
                    ls.update(payload.model_dump(exclude_unset=True, mode="json"))
                    return save_builder_curriculum(db, obj, section_id, lesson_id)
    from fastapi import HTTPException; raise HTTPException(404, "Lesson not found")

@router.delete("/{training_id}/sections/{section_id}/lessons/{lesson_id}")
def delete_lesson(training_id: UUID, section_id: str, lesson_id: str, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    from app.repository.training_repo import get_training_by_id
    from sqlalchemy.orm.attributes import flag_modified
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    for s in obj.sections or []:
        if s.get("id")==section_id:
            s["lessons"]=[l for l in s.get("lessons",[]) if l.get("id")!=lesson_id]; flag_modified(obj, "sections"); db.commit(); return {"message":"Deleted"}
    from fastapi import HTTPException; raise HTTPException(404, "Section not found")

@router.delete(
    "/{training_id}/sections/{section_id}/lessons/{lesson_id}/media",
    summary="Remove one attachment from a lesson media list",
    description=(
        "Removes a single attachment URL from a lesson's documents, videos, or notes list. "
        "Use this when emptying a list client-side is not possible — the lesson PUT merges, "
        "so omitted lists are preserved. Returns the updated lesson."
    ),
)
def delete_lesson_media(training_id: UUID, section_id: str, lesson_id: str, kind: str = Query("documents", description="documents | videos | notes"), url: str = Query(..., description="Attachment URL to remove (for documents: the item's url)"), db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    from app.repository.training_repo import get_training_by_id
    if kind not in ("documents", "videos", "notes"):
        from fastapi import HTTPException; raise HTTPException(422, f"kind must be one of documents|videos|notes, got '{kind}'")
    obj = get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    for s in obj.sections or []:
        if s.get("id")==section_id:
            for ls in s.get("lessons", []):
                if ls.get("id")==lesson_id:
                    items = ls.get(kind) or []
                    kept = [i for i in items if (i.get("url") if isinstance(i, dict) else i) != url]
                    if len(kept) == len(items):
                        from fastapi import HTTPException; raise HTTPException(404, f"Attachment not found in lesson {kind}")
                    ls[kind] = kept
                    return save_builder_curriculum(db, obj, section_id, lesson_id)
            from fastapi import HTTPException; raise HTTPException(404, "Lesson not found")
    from fastapi import HTTPException; raise HTTPException(404, "Section not found")

# Enrol, waitlist, assessments, assignments, progress, live-sessions, announcements
@router.get("/my/enrolments", summary="Participant dashboard — enrolled/active/completed/cancelled")
def my_enrolments(status: str | None = Query(None, description="enrolled|pending_approval|cancelled|waitlisted"), db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services.training_service import list_my_enrolments_service
    email = current_user.get("email")
    if not email:
        from fastapi import HTTPException; raise HTTPException(400, "Email not found in token")
    return list_my_enrolments_service(db, email, status_filter=status)

@router.get("/my/wishlist", response_model=list[TrainingWishlistItemResponse], summary="My saved/wishlisted trainings")
def my_wishlist(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return list_training_wishlist_service(db, UUID(str(current_user["id"])))

@router.post("/{training_id}/enrol", status_code=201, response_model=TrainingEnrolmentResponse | TrainingEnrolWaitlistResponse, summary="Enrol in Training")
def enrol(training_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services.training_service import create_training_enrol_service
    coupon = payload.get("coupon_code")
    return create_training_enrol_service(db, training_id, payload, coupon_code=coupon, current_user=current_user)


@router.post(
    "/{training_id}/enroll",
    status_code=201,
    response_model=TrainingEnrolmentResponse | TrainingEnrolWaitlistResponse,
    summary="Enroll in Training (alias of /enrol)",
    description="Identical to POST /{training_id}/enrol — American-spelling alias for frontend clients that call /enroll.",
)
def enroll(training_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return enrol(training_id, payload, db, current_user)


@router.post("/{training_id}/reviews", response_model=TrainingReviewResponse, status_code=201, summary="Rate & review — verified (must be enrolled)")
def create_review(training_id: UUID, payload: TrainingReviewCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from fastapi import HTTPException
    email = current_user.get("email")
    if not email:
        raise HTTPException(403, "Enrolled participants only")
    payload = payload.model_copy(update={"participant_email": email})
    return create_training_review_service(db, training_id, payload)


@router.get("/{training_id}/reviews", response_model=TrainingReviewListResponse, summary="List reviews with average rating")
def list_reviews(training_id: UUID, db: Session = Depends(get_db)):
    return list_training_reviews_service(db, training_id)


@router.post("/{training_id}/wishlist", response_model=TrainingWishlistItemResponse, status_code=201, summary="Save training to wishlist")
def add_wishlist(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return add_training_wishlist_service(db, UUID(str(current_user["id"])), training_id)


@router.delete("/{training_id}/wishlist", summary="Remove training from wishlist")
def remove_wishlist(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return remove_training_wishlist_service(db, UUID(str(current_user["id"])), training_id)


@router.get("/{training_id}/enrolments")
def list_enrolments(training_id: UUID, db: Session=Depends(get_db), current_user: dict = Depends(require_training_manager)):
    from app.models.training_model import TrainingEnrolment
    rows = db.query(TrainingEnrolment).filter(TrainingEnrolment.training_id==training_id).all()
    return [
        {
            "id": str(r.id),
            "training_id": str(r.training_id),
            "participant_name": r.participant_name,
            "participant_email": r.participant_email,
            "group_enrol": r.group_enrol,
            "status": r.status,
            "coupon_code": r.coupon_code,
            "access_expires_at": r.access_expires_at.isoformat() if r.access_expires_at else None,
            "qr_code": r.qr_code,
            "checked_in_at": r.checked_in_at.isoformat() if r.checked_in_at else None,
            "checked_out_at": r.checked_out_at.isoformat() if r.checked_out_at else None,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]

@router.get("/{training_id}/content", summary="Secure enrolled content — draft/preview/release gated")
def secure_content(training_id: UUID, db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    return get_secure_training_content_service(db, training_id, current_user)

@router.get("/{training_id}/downloads", summary="Offline-download manifest — lessons cacheable for offline viewing")
def list_downloads(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return list_downloadable_lessons_service(db, training_id, current_user)

@router.get("/{training_id}/sections/{section_id}/lessons/{lesson_id}/download", summary="Download URL for one offline-enabled lesson")
def download_lesson(training_id: UUID, section_id: str, lesson_id: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return get_lesson_download_service(db, training_id, section_id, lesson_id, current_user)

@router.get("/{training_id}/notes.pdf", summary="Auto-generated course notes PDF — offline reading")
def download_notes_pdf(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from fastapi.responses import StreamingResponse
    from io import BytesIO
    pdf_bytes = generate_training_notes_pdf_service(db, training_id, current_user)
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=training_{training_id}_notes.pdf"},
    )

@router.delete("/{training_id}/enrolments/{enrol_id}", summary="Cancel enrolment — access-expiry & waitlist")
def cancel_enrol(request: Request, training_id: UUID, enrol_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services.training_service import cancel_training_enrol_service
    email=current_user.get("email")
    # allow staff to cancel any enrolment — but only within their own tenant's training
    if current_user.get("role") in ["admin","provider","super_admin"]:
        _require_tenant_ownership_if_staff(request, training_id, db, current_user)
        return cancel_training_enrol_service(db, training_id, enrol_id)
    return cancel_training_enrol_service(db, training_id, enrol_id, participant_email=email)

@router.post("/{training_id}/enrolments/{enrol_id}/approve", summary="Approve/Reject enrolment with optional reason")
def approve_enrol(training_id: UUID, enrol_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    action = payload.get("action", "approve")
    reason = payload.get("reason")
    return approve_training_enrol_service(db, training_id, enrol_id, action, reason=reason, current_user=current_user)

@router.post("/{training_id}/enrolments/validate-qr", response_model=TrainingValidateQrResponse, summary="Validate an enrolment QR code (read-only scan)")
def validate_enrolment_qr(training_id: UUID, payload: TrainingValidateQrRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    from app.services.training_service import validate_training_qr_service
    return validate_training_qr_service(db, training_id, payload.qr_code)

@router.post("/{training_id}/enrolments/check-in", response_model=TrainingEnrolCheckInResponse, summary="Check in a participant by enrolment_id or qr_code")
def check_in_enrolment(training_id: UUID, payload: TrainingEnrolCheckInRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    from app.services.training_service import check_in_enrolment_service
    return check_in_enrolment_service(db, training_id, payload.enrolment_id, payload.qr_code, current_user)

@router.post("/{training_id}/enrolments/uncheck-in", response_model=TrainingEnrolUncheckInResponse, summary="Undo a participant check-in")
def uncheck_in_enrolment(training_id: UUID, payload: TrainingEnrolUncheckInRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    from app.services.training_service import uncheck_in_enrolment_service
    return uncheck_in_enrolment_service(db, training_id, payload.enrolment_id, payload.qr_code)

@router.get("/{training_id}/enrolments/check-in-preview", response_model=list[TrainingCheckInPreviewItem], summary="Batch check-in — list enrolments with eligibility computed server-side")
def enrolment_check_in_preview(training_id: UUID, status_filter: str | None = Query(None, alias="status", description="Filter: enrolled|attended|cancelled|waitlisted"), db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    from app.services.training_service import list_training_checkin_preview_service
    return list_training_checkin_preview_service(db, training_id, status_filter)

@router.post("/{training_id}/batch-check-in", response_model=TrainingBatchCheckInResponse, summary="Batch check-in multiple participants")
def batch_check_in_enrolments(training_id: UUID, payload: TrainingBatchCheckInRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    from app.services.training_service import batch_check_in_training_enrolments_service
    return batch_check_in_training_enrolments_service(db, training_id, payload.participants, current_user)

@router.post("/{training_id}/checkout", status_code=201, summary="Checkout — Training")
def checkout_training(training_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services.training_service import create_training_checkout_service
    from app.schemas.training_schema import TrainingCheckoutRequest
    # allow dict or typed
    req = TrainingCheckoutRequest(**payload) if isinstance(payload, dict) else payload
    return create_training_checkout_service(db, training_id, req)

@router.get("/{training_id}/orders", summary="List Training Orders")
def list_training_orders(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    from app.services.training_service import get_training_orders_service
    return get_training_orders_service(db, training_id)

@router.post("/{training_id}/waitlist", status_code=201, summary="Join waitlist — dedup + capacity aware")
def join_waitlist(training_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services.training_service import join_waitlist_service
    return join_waitlist_service(db, training_id, payload, current_user)

@router.delete("/{training_id}/waitlist/{entry_id}")
def leave_waitlist(request: Request, training_id: UUID, entry_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services.training_service import leave_waitlist_service
    is_staff = current_user and current_user.get("role") in ("admin", "provider", "super_admin")
    if is_staff:
        _require_tenant_ownership_if_staff(request, training_id, db, current_user)
    email = current_user.get("email") if current_user and not is_staff else None
    return leave_waitlist_service(db, training_id, entry_id, participant_email=email)

@router.post("/{training_id}/assessments", status_code=201, summary="Create quizzes/tests/assessments/surveys — supports pre-course/module/final/feedback level, pass/attempt/time, publication, randomise")
def create_assessment(training_id: UUID, payload: AssessmentCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    from app.repository.training_repo import get_training_by_id
    import uuid
    t = get_training_by_id(db, training_id)
    if not t: from fastapi import HTTPException; raise HTTPException(404, "Training not found")
    arr = list(t.assessments or []); new={"id": str(uuid.uuid4()), **payload.model_dump(mode="json")}; arr.append(new); t.assessments=arr; db.commit(); return new

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

@router.get(
    "/{training_id}/assessments/{aid}",
    summary="Get assessment details by ID — full question set for taking/reviewing an assessment",
    responses={
        200: {
            "description": "Assessment with its questions. `correct_answer`/`explanation` are included for admin/provider only — learners get the same question set with those two fields stripped.",
            "content": {
                "application/json": {
                    "example": {
                        "id": "8f14e45f-ceea-4c19-b0a9-3fb6dbe1e6a1",
                        "title": "Module 1 Quiz",
                        "module_id": "module-1",
                        "lesson_id": None,
                        "pass_percent": 70,
                        "attempts_allowed": 3,
                        "attempts_made": 1,
                        "time_limit_minutes": 20,
                        "publication": "immediate",
                        "questions": [
                            {
                                "id": "q1",
                                "question_text": "Which planet is known as the Red Planet?",
                                "question_type": "mcq",
                                "options": ["Earth", "Mars", "Jupiter", "Venus"],
                                "correct_answer": "Mars",
                                "points": 1,
                                "explanation": "Mars appears red due to iron oxide on its surface.",
                            },
                            {
                                "id": "q2",
                                "question_text": "Select all prime numbers.",
                                "question_type": "multiple_select",
                                "options": ["2", "3", "4", "9"],
                                "correct_answer": "2,3",
                                "points": 2,
                                "explanation": None,
                            },
                            {
                                "id": "q3",
                                "question_text": "The sky is blue due to Rayleigh scattering.",
                                "question_type": "true_false",
                                "options": ["True", "False"],
                                "correct_answer": "True",
                                "points": 1,
                                "explanation": None,
                            },
                            {
                                "id": "q4",
                                "question_text": "What is the capital of France?",
                                "question_type": "short_answer",
                                "options": None,
                                "correct_answer": None,
                                "points": 1,
                                "explanation": "Manually graded — free text answer.",
                            },
                            {
                                "id": "q5",
                                "question_text": "Explain the water cycle in your own words.",
                                "question_type": "essay",
                                "options": None,
                                "correct_answer": None,
                                "points": 5,
                                "explanation": "Manually graded — free text answer.",
                            },
                        ],
                    }
                }
            },
        },
        404: {"description": "Training or assessment not found"},
    },
)
def get_assessment(training_id: UUID, aid: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services.training_service import get_assessment_service
    return get_assessment_service(db, training_id, aid, current_user)

@router.put("/{training_id}/assessments/{aid}", summary="Update assessment metadata")
def update_assessment(training_id: UUID, aid: str, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    return update_assessment_service(db, training_id, aid, payload)

@router.delete("/{training_id}/assessments/{aid}", summary="Delete assessment")
def delete_assessment(training_id: UUID, aid: str, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    return delete_assessment_service(db, training_id, aid)

@router.get("/{training_id}/question-bank", summary="Question bank — reusable questions")
def question_bank(training_id: UUID, db: Session=Depends(get_db), current_user: dict = Depends(require_training_manager)):
    from app.services.training_service import get_question_bank_service
    return get_question_bank_service(db, training_id)

@router.post("/{training_id}/assessments/{aid}/questions", status_code=201)
def add_question(training_id: UUID, aid: str, payload: AssessmentQuestionCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    return add_assessment_question_service(db, training_id, aid, payload)

@router.delete("/{training_id}/assessments/{aid}/questions/{qid}", summary="Delete assessment question")
def delete_question(training_id: UUID, aid: str, qid: str, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    return delete_assessment_question_service(db, training_id, aid, qid)

@router.post("/{training_id}/assessments/{aid}/submit", status_code=201, response_model=AssessmentSubmitResponse, summary="Submit — automatic scoring, pass/attempt/time enforced")
def submit_assessment(training_id: UUID, aid: str, payload: AssessmentSubmitCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    email = current_user.get("email") if current_user else "user@example.com"
    return submit_assessment_service(db, training_id, aid, payload, participant_email=email)

@router.post("/{training_id}/assessments/{aid}/submissions/{sid}/grade", summary="Manual evaluation for written answers")
def grade_assessment(training_id: UUID, aid: str, sid: UUID, payload: dict, db: Session=Depends(get_db), current_user: dict = Depends(require_training_manager)):
    from app.services.training_service import grade_assessment_manual_service
    return grade_assessment_manual_service(db, training_id, aid, str(sid), int(payload.get("score") or payload.get("grade") or 0), payload.get("feedback"))

@router.get("/{training_id}/assessments/{aid}/submissions/{sid}/review", response_model=AssessmentReviewResponse, summary="Answer explanations & result review")
def review_assessment(training_id: UUID, aid: str, sid: UUID, db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services.training_service import get_assessment_result_service
    return get_assessment_result_service(db, training_id, aid, str(sid), current_user)

@router.get(
    "/{training_id}/assignments",
    response_model=list[TrainingAssignmentResponse],
    summary="List training assignments — visible to any enrolled/authenticated user, not just admin/provider",
)
def list_assignments(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return list_training_assignments_service(db, training_id, current_user)

@router.post("/{training_id}/assignments", status_code=201, response_model=TrainingAssignmentResponse)
def create_assignment(training_id: UUID, payload: AssignmentCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    return create_assignment_service(db, training_id, payload)

@router.delete("/{training_id}/assignments/{aid}", summary="Delete training assignment")
def delete_assignment(training_id: UUID, aid: str, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    return delete_training_assignment_service(db, training_id, aid)

@router.post("/{training_id}/assignments/{aid}/submit", status_code=201, response_model=AssignmentSubmitResponse, summary="Submit text/links/images/videos/documents — resubmission allowed")
def submit_assignment(training_id: UUID, aid: str, payload: AssignmentSubmitCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    email = current_user.get("email") if current_user else "user@example.com"
    return submit_assignment_service(db, training_id, aid, payload, participant_email=email)

@router.post("/{training_id}/assignments/{aid}/submissions/{sid}/grade", summary="Instructor feedback & grading")
def grade_assignment(training_id: UUID, aid: str, sid: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    from app.services.training_service import grade_assignment_service
    return grade_assignment_service(db, training_id, aid, str(sid), payload.get("grade") or "0", payload.get("feedback"))

@router.post("/{training_id}/progress/complete-lesson", response_model=TrainingCompleteLessonResponse, summary="Mark a lesson complete — updates overall progress and resume position")
def complete_lesson(training_id: UUID, payload: TrainingCompleteLessonRequest, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services.training_service import complete_lesson_service
    email = payload.participant_email or (current_user.get("email") if current_user else None)
    if not email: from fastapi import HTTPException; raise HTTPException(400, "participant_email required")
    return complete_lesson_service(db, training_id, payload.lesson_id, email)

@router.get("/{training_id}/live-sessions/{session_id}/attendance", summary="List live session attendance")
def get_live_attendance(training_id: UUID, session_id: str, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    return get_live_attendance_service(db, training_id, session_id)

@router.get("/{training_id}/live-sessions/{session_id}/attendance/export", summary="Export live session attendance CSV")
def export_live_attendance(training_id: UUID, session_id: str, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    from fastapi.responses import StreamingResponse
    csv_content, sid = export_live_attendance_service(db, training_id, session_id)
    return StreamingResponse(iter([csv_content]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=training_{training_id}_session_{sid}_attendance.csv"})

@router.post("/{training_id}/live-sessions/{session_id}/attendance", summary="Record live session attendance")
def live_attendance(request: Request, training_id: UUID, session_id: str, payload: dict | None = None, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from fastapi import HTTPException
    from app.services.training_service import validate_training_qr_service
    qr_code = (payload or {}).get("qr_code")
    email = (payload or {}).get("participant_email")
    if qr_code and not email:
        # Admin physical-venue scan path: resolves the learner via the same
        # training-scoped, cancelled/expired-checked QR lookup validate-qr already
        # uses, then records attendance through the same call as self-attendance.
        email = validate_training_qr_service(db, training_id, qr_code)["participant_email"]
    email = email or current_user.get("email")
    if not email:
        raise HTTPException(400, "participant_email required")
    if email != current_user.get("email"):
        require_training_manager(request, training_id, db, current_user)
    result = record_live_attendance_service(db, training_id, session_id, email)
    return {**result, "status": "success", "message": "Attendance recorded",
            "attendance": {"joined_at": result["recorded_at"], "participant_email": email}}

@router.get("/{training_id}/certificate", summary="Digital completion certificate")
def get_certificate(training_id: UUID, participant_email: str | None = Query(None), db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    participant_email = participant_email or (current_user.get("email") if current_user else None)
    if not participant_email:
        from fastapi import HTTPException; raise HTTPException(status_code=400, detail="participant_email required")
    # Only owner or admin/provider can fetch certificate
    if current_user and current_user.get("role") not in ("admin", "provider") and current_user.get("email") != participant_email:
        from fastapi import HTTPException; raise HTTPException(status_code=403, detail="Not authorized to view this certificate")
    from app.services.training_service import get_certificate_service
    return get_certificate_service(db, training_id, participant_email)

@router.get("/{training_id}/certificate.pdf", summary="Certificate of Completion — real generated PDF")
def download_certificate_pdf(training_id: UUID, participant_email: str | None = Query(None), db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from fastapi.responses import StreamingResponse
    from io import BytesIO
    from app.services.training_service import generate_certificate_pdf_service
    participant_email = participant_email or (current_user.get("email") if current_user else None)
    if not participant_email:
        from fastapi import HTTPException; raise HTTPException(status_code=400, detail="participant_email required")
    if current_user and current_user.get("role") not in ("admin", "provider") and current_user.get("email") != participant_email:
        from fastapi import HTTPException; raise HTTPException(status_code=403, detail="Not authorized to view this certificate")
    pdf_bytes = generate_certificate_pdf_service(db, training_id, participant_email)
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=training_{training_id}_certificate.pdf"},
    )

@router.get("/{training_id}/progress")
def progress(training_id: UUID, participant_email: str | None = Query(None), db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    email = participant_email or (current_user.get("email") if current_user else None)
    if participant_email and current_user and current_user.get("role") not in ("admin", "provider") and current_user.get("email") != participant_email:
        from fastapi import HTTPException; raise HTTPException(status_code=403, detail="Not authorized to view this participant's progress")
    return get_training_progress_service(db, training_id, participant_email=email)

@router.get("/{training_id}/dashboards/participant")
def dash_participant(training_id: UUID, participant_email: str | None = Query(None), db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services.training_service import get_training_participant_dashboard_service
    email = participant_email or (current_user.get("email") if current_user else None)
    return get_training_participant_dashboard_service(db, training_id, participant_email=email)

@router.get("/{training_id}/dashboards/provider")
def dash_provider(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    from app.services.training_service import get_training_provider_dashboard_service
    return get_training_provider_dashboard_service(db, training_id)

@router.get("/{training_id}/reports", summary="Training reports with optional date range")
def training_reports(training_id: UUID, type: str = Query("enrolment", description="enrolment|attendance|engagement|assessment|progress|completion|revenue"), date_from: str | None = Query(None), date_to: str | None = Query(None), db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    from app.services.training_service import get_training_reports_service
    return get_training_reports_service(db, training_id, type, date_from=date_from, date_to=date_to)

@router.post("/{training_id}/check-in", summary="Participant self-check-in via pass_code/qr_payload")
def check_in(training_id: UUID, payload: TrainingCheckInRequest, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return check_in_training_service(db, training_id, payload.participant_email, payload.pass_code)

@router.post("/{training_id}/live-sessions", status_code=201)
def create_live(training_id: UUID, payload: TrainingLiveSessionCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
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
from app.schemas.training_schema import TrainingDiscussionCreate, TrainingDiscussionReply, TrainingDiscussionResponse

@router.get("/{training_id}/discussions", response_model=list[TrainingDiscussionResponse], summary="Discussion — Q&A list")
def list_discussions(training_id: UUID, db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.repository.training_repo import get_training_by_id
    obj=get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404,"Training not found")
    return getattr(obj, "discussions", []) or []

@router.post("/{training_id}/discussions", response_model=TrainingDiscussionResponse, status_code=201, summary="Post Q&A")
def post_discussion(training_id: UUID, payload: TrainingDiscussionCreate, db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.repository.training_repo import get_training_by_id
    from sqlalchemy.orm.attributes import flag_modified
    import uuid as _uuid
    obj=get_training_by_id(db, training_id)
    if not obj: from fastapi import HTTPException; raise HTTPException(404,"Training not found")
    disc=list(getattr(obj, "discussions", []) or [])
    
    question_text = payload.question or payload.text or ""
    entry={"id": str(_uuid.uuid4()), "author": current_user.get("email","anonymous"), "question": question_text, "answer": None, "created_at": __import__("datetime").datetime.utcnow().isoformat()}
    if not entry["question"].strip():
        from fastapi import HTTPException; raise HTTPException(400, "Question is required")
    disc.append(entry)
    obj.discussions=disc
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(obj, "discussions")
    db.commit()
    return entry

@router.post("/{training_id}/discussions/{discussion_id}/replies", response_model=TrainingDiscussionResponse, status_code=201, summary="Reply to Q&A / mark answer")
def reply_discussion(training_id: UUID, discussion_id: str, payload: TrainingDiscussionReply, db: Session=Depends(get_db), current_user: dict = Depends(get_current_user)):
    return reply_discussion_service(db, training_id, discussion_id, payload.model_dump(), current_user)

@router.post("/{training_id}/announcements", summary="Create persisted announcement")
def announce(training_id: UUID, payload: AnnouncementCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
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
    current_user: dict = Depends(require_training_manager),
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


@router.get("/{training_id}/admin-notes", summary="Latest super-admin reject/request-changes message")
def get_training_admin_notes(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    return get_training_admin_notes_service(db, training_id)

@router.post("/{training_id}/resubmit", response_model=TrainingResponse, summary="Resubmit training after requested changes")
def resubmit_training(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    from app.repository.training_repo import get_training_by_id
    from fastapi import HTTPException
    training = get_training_by_id(db, training_id)
    if not training:
        raise HTTPException(status_code=404, detail="Training not found")
    if training.status not in ("needs_revision", "draft", "rejected"):
        raise HTTPException(status_code=400, detail=f"Cannot resubmit training in '{training.status}' status")
    return update_training_status_service(db, training_id, "pending_approval", current_user)


@router.post(
    "/{training_id}/orders/{order_id}/refund/approve",
    summary="Approve or Reject Training Refund (Admin/Provider)",
)
def approve_training_refund(
    training_id: UUID,
    order_id: UUID,
    payload: TrainingRefundApproveRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_training_manager),
):
    return approve_training_refund_service(db, training_id, order_id, payload)


@router.get("/{training_id}/enrolments/export", summary="Export training enrolments CSV")
def export_enrolments(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_training_manager)):
    from fastapi.responses import StreamingResponse
    from app.services.training_service import export_training_enrolments_service
    content = export_training_enrolments_service(db, training_id, current_user)
    return StreamingResponse(iter([content]), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=training_{training_id}_enrolments.csv"})
