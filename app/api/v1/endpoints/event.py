from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, require_roles
from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.event_schema import (
    EventCreate,
    EventDetailResponse,
    EventPaginatedResponse,
    EventResponse,
    EventStatusUpdate,
    EventUpdate,
)
from app.schemas.event_schema import EventCheckInRequest, EventCheckInResponse, EventRegistrationCreate, EventSessionCreate, EventSessionUpdate
from app.services.event_service import (
    add_session_service,
    create_event_service,
    create_feedback_service,
    create_registration_service,
    create_template_service,
    create_waitlist_entry_service,
    delete_event_service,
    delete_session_service,
    delete_waitlist_entry_service,
    duplicate_event_service,
    get_event_attendance_service,
    get_event_feedbacks_service,
    get_event_registrations_service,
    get_event_reports_service,
    get_event_service,
    get_event_waitlist_service,
    get_events_service,
    get_sessions_service,
    moderate_review_service,
    send_announcement_service,
    update_event_service,
    update_event_status_service,
    update_session_service,
)

router = APIRouter(tags=["Events"])


@router.post("/", response_model=EventResponse, status_code=status.HTTP_201_CREATED, summary="Create Event")
def create_event(event: EventCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return create_event_service(db, event)


@router.get("/", response_model=EventPaginatedResponse, status_code=status.HTTP_200_OK, summary="List Events")
def list_events(
    search: str | None = Query(None, description="Search across title/description/category."),
    category: str | None = Query(None, description="Filter by category."),
    tenant_id: UUID | None = Query(None, description="Filter by tenant ID."),
    enterprise_id: UUID | None = Query(None, description="Filter by enterprise ID."),
    location_id: UUID | None = Query(None, description="Filter by location ID."),
    status_filter: str | None = Query(None, alias="status", description="Filter by status."),
    delivery_mode: str | None = Query(None, description="Filter by delivery mode."),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
):
    return get_events_service(
        db,
        search=search,
        category=category,
        tenant_id=tenant_id,
        enterprise_id=enterprise_id,
        location_id=location_id,
        status_filter=status_filter,
        delivery_mode=delivery_mode,
        page=page,
        page_size=page_size,
    )


@router.get("/{event_id}", response_model=EventDetailResponse, status_code=status.HTTP_200_OK, summary="Get Event by ID")
def get_event(event_id: UUID = Path(..., description="Event ID"), db: Session = Depends(get_db)):
    return get_event_service(db, event_id)


@router.put("/{event_id}", response_model=EventResponse, status_code=status.HTTP_200_OK, summary="Update Event")
def update_event(event: EventUpdate, event_id: UUID = Path(..., description="Event ID"), db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return update_event_service(db, event_id, event)


@router.delete("/{event_id}", status_code=status.HTTP_200_OK, summary="Delete Event")
def delete_event(event_id: UUID = Path(..., description="Event ID"), db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    delete_event_service(db, event_id)
    return {"message": "Event deleted successfully"}


@router.post("/{event_id}/duplicate", response_model=EventResponse, status_code=status.HTTP_201_CREATED, summary="Duplicate Event")
def duplicate_event(event_id: UUID = Path(..., description="Event ID"), db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return duplicate_event_service(db, event_id)


@router.patch("/{event_id}/status", response_model=EventResponse, status_code=status.HTTP_200_OK, summary="Update Event Status")
def update_status(event_id: UUID, payload: EventStatusUpdate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return update_event_status_service(db, event_id, payload.status)


@router.post("/{event_id}/unpublish", response_model=EventResponse, status_code=status.HTTP_200_OK, summary="Unpublish Event")
def unpublish_event(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return update_event_status_service(db, event_id, "draft")


@router.post("/{event_id}/archive", response_model=EventResponse, status_code=status.HTTP_200_OK, summary="Archive Event")
def archive_event(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return update_event_status_service(db, event_id, "completed")


# ---- Registrations & Waitlist (E7-E10) ----


@router.get("/{event_id}/registrations", summary="List Registrations")
def list_registrations(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return get_event_registrations_service(db, event_id)


@router.post("/{event_id}/registrations", status_code=status.HTTP_201_CREATED, summary="Register for Event")
def register(event_id: UUID, payload: EventRegistrationCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return create_registration_service(db, event_id, payload)


@router.delete("/{event_id}/registrations/{reg_id}", summary="Cancel Registration")
def cancel_registration(event_id: UUID, reg_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.models.event_aux_models import EventRegistration
    from fastapi import HTTPException

    reg = db.query(EventRegistration).filter(EventRegistration.id == reg_id, EventRegistration.event_id == event_id).first()
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found")
    reg.status = "cancelled"
    db.commit()
    return {"message": "Registration cancelled"}


@router.get("/{event_id}/registrations/export", summary="Export Registrations CSV")
def export_registrations(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from fastapi.responses import StreamingResponse
    import csv, io

    regs = get_event_registrations_service(db, event_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "name", "email", "status", "qr_code"])
    for r in regs:
        writer.writerow([r.id, r.participant_name, r.participant_email, r.status, r.qr_code])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=event_{event_id}_registrations.csv"})


@router.get("/{event_id}/waitlist", summary="List Waitlist")
def list_waitlist(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return get_event_waitlist_service(db, event_id)


@router.post("/{event_id}/waitlist", status_code=status.HTTP_201_CREATED, summary="Join Waitlist")
def join_waitlist(event_id: UUID, payload: EventRegistrationCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return create_waitlist_entry_service(db, event_id, payload)


@router.delete("/{event_id}/waitlist/{entry_id}", summary="Leave Waitlist")
def leave_waitlist(event_id: UUID, entry_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return delete_waitlist_entry_service(db, event_id, entry_id)


# ---- Sessions & Attendance (E11-E12) ----


@router.get("/{event_id}/sessions", summary="List Sessions")
def list_sessions(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return get_sessions_service(db, event_id)


@router.post("/{event_id}/sessions", status_code=status.HTTP_201_CREATED, summary="Add Session")
def add_session(event_id: UUID, payload: EventSessionCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return add_session_service(db, event_id, payload)


@router.put("/{event_id}/sessions/{session_id}", summary="Update Session")
def update_session(event_id: UUID, session_id: str, payload: EventSessionUpdate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return update_session_service(db, event_id, session_id, payload)


@router.delete("/{event_id}/sessions/{session_id}", summary="Delete Session")
def delete_session(event_id: UUID, session_id: str, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return delete_session_service(db, event_id, session_id)


@router.post("/{event_id}/check-in", summary="Check-in Participant")
def check_in(event_id: UUID, payload: EventCheckInRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.models.event_aux_models import EventRegistration
    from fastapi import HTTPException
    from datetime import datetime

    # Find registration by ID or QR code
    reg = None
    if payload.registration_id:
        reg = db.query(EventRegistration).filter(
            EventRegistration.id == payload.registration_id,
            EventRegistration.event_id == event_id
        ).first()
    elif payload.qr_code:
        reg = db.query(EventRegistration).filter(
            EventRegistration.qr_code == payload.qr_code,
            EventRegistration.event_id == event_id
        ).first()
    else:
        raise HTTPException(status_code=400, detail="registration_id or qr_code is required")

    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found")
    if reg.status == "cancelled":
        raise HTTPException(status_code=400, detail="Cannot check-in: registration is cancelled")
    if reg.status == "attended":
        return EventCheckInResponse(
            message="Already checked in",
            registration_id=reg.id,
            participant_name=reg.participant_name,
            participant_email=reg.participant_email,
            status=reg.status,
            checked_in_at=reg.checked_in_at.isoformat() if reg.checked_in_at else None,
            session_id=payload.session_id
        )

    reg.status = "attended"
    reg.checked_in_at = datetime.utcnow()
    reg.checked_in_by = current_user.get("id")
    if payload.session_id:
        reg.session_id = payload.session_id
    db.commit()
    db.refresh(reg)

    return EventCheckInResponse(
        message="Checked in successfully",
        registration_id=reg.id,
        participant_name=reg.participant_name,
        participant_email=reg.participant_email,
        status=reg.status,
        checked_in_at=reg.checked_in_at.isoformat() if reg.checked_in_at else None,
        session_id=reg.session_id
    )


@router.get("/{event_id}/attendance", summary="Attendance Report")
def attendance(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return get_event_attendance_service(db, event_id)


# ---- Announcements, Feedback, Reviews, Reports, Templates (E13-E16) ----


@router.post("/{event_id}/announcements", status_code=status.HTTP_201_CREATED, summary="Send Announcement")
def announce(event_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return send_announcement_service(db, event_id, payload.get("message", ""))


@router.post("/{event_id}/feedback", status_code=status.HTTP_201_CREATED, summary="Submit Feedback")
def submit_feedback(event_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return create_feedback_service(db, event_id, payload, is_review=False)


@router.get("/{event_id}/feedback", summary="List Feedback")
def list_feedback(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return get_event_feedbacks_service(db, event_id, is_review=False)


@router.post("/{event_id}/reviews", status_code=status.HTTP_201_CREATED, summary="Submit Review")
def submit_review(event_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return create_feedback_service(db, event_id, payload, is_review=True)


@router.patch("/{event_id}/reviews/{review_id}/moderate", summary="Moderate Review")
def moderate_review(event_id: UUID, review_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin"]))):
    return moderate_review_service(db, review_id, payload.get("action", "approved"))


@router.get("/{event_id}/reports", summary="Event Reports")
def reports(event_id: UUID, type: str = Query("registration"), format: str = Query("json"), db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return get_event_reports_service(db, event_id, type)


@router.get("/reports/summary", summary="Performance Dashboard")
def reports_summary(enterprise_id: UUID | None = None, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.models.event_model import Event

    q = db.query(Event).filter(Event.is_deleted.is_(False))
    if enterprise_id:
        q = q.filter(Event.enterprise_id == enterprise_id)
    total = q.count()
    return {"total_events": total, "by_status": {}}


@router.post("/templates", status_code=status.HTTP_201_CREATED, summary="Create Template")
def create_template(payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return create_template_service(db, payload)


@router.get("/templates", summary="List Templates")
def list_templates(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.models.event_aux_models import EventTemplate

    return db.query(EventTemplate).all()


@router.post("/templates/{template_id}/apply", status_code=status.HTTP_201_CREATED, summary="Apply Template")
def apply_template(template_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.models.event_aux_models import EventTemplate
    from fastapi import HTTPException
    from app.models.event_model import Event

    tmpl = db.query(EventTemplate).filter(EventTemplate.id == template_id).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")
    data = dict(tmpl.template_data)
    data["enterprise_id"] = payload.get("enterprise_id") or data.get("enterprise_id")
    data["status"] = "draft"
    # Regenerate session ids for cloned template so they are addressable via PUT/DELETE
    if data.get("sessions"):
        import copy
        import uuid

        cloned_sessions = copy.deepcopy(data["sessions"])
        for s in cloned_sessions:
            if isinstance(s, dict):
                s["id"] = str(uuid.uuid4())
                sd = s.get("session_date")
                if hasattr(sd, "isoformat"):
                    s["session_date"] = sd.isoformat()
        data["sessions"] = cloned_sessions
    event = Event(**{k: v for k, v in data.items() if k in [c.key for c in Event.__table__.columns]})
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
