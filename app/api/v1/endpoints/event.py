from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_admin, get_current_super_admin, get_current_user, require_roles
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
from app.schemas.event_schema import (
    EventAnnouncementCreate,
    EventBatchCheckInRequest,
    EventCheckInRequest,
    EventCheckOutRequest,
    EventCheckoutRequest,
    EventOrderResponse,
    EventRefundRequest,
    EventRegistrationCreate,
    EventSessionCreate,
    EventSessionUpdate,
    EventTemplateApplyRequest,
    EventTemplateCreateRequest,
    EventBatchCheckInPreviewItem,
    EventBatchCheckInResponse,
    EventTemplateDeleteResponse,
    EventTemplateResponse,
    EventTemplateUpdateRequest,
    EventUncheckInRequest,
)
from app.services.event_service import (
    get_template_service,
    add_session_service,
    apply_template_service,
    check_in_service,
    check_out_service,
    contact_organiser_service,
    create_event_checkout_service,
    create_event_refund_service,
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
    get_event_orders_service,
    get_event_registrations_service,
    get_event_reports_service,
    get_event_service,
    get_event_summary_service,
    get_event_waitlist_service,
    get_events_service,
    get_meeting_link_service,
    get_sessions_service,
    list_templates_service,
    moderate_review_service,
    my_registrations_service,
    send_announcement_service,
    uncheck_in_service,
    update_event_service,
    update_event_status_service,
    update_session_service,
    validate_qr_service,
)

router = APIRouter(tags=["Events"])


@router.post("/", response_model=EventResponse, status_code=status.HTTP_201_CREATED, summary="Create Event")
def create_event(event: EventCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return create_event_service(db, event, current_user)


@router.get("/", response_model=EventPaginatedResponse, status_code=status.HTTP_200_OK, summary="List Events")
def list_events(
    search: str | None = Query(None, description="Search across title/description/category."),
    category: str | None = Query(None, description="Filter by category."),
    tenant_id: UUID | None = Query(
        None,
        description="Filter events by tenant ID. Omit for global list (all tenants).",
    ),
    enterprise_id: UUID | None = Query(None, description="Filter by enterprise ID."),
    location_id: UUID | None = Query(None, description="Filter by location ID."),
    status_filter: str | None = Query(None, alias="status", description="Filter by status."),
    delivery_mode: str | None = Query(None, description="Filter by delivery mode."),
    date_from: str | None = Query(None, description="Filter start_date >= YYYY-MM-DD"),
    date_to: str | None = Query(None, description="Filter end_date <= YYYY-MM-DD"),
    min_price: str | None = Query(None, description="Min price"),
    max_price: str | None = Query(None, description="Max price"),
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
        date_from=date_from,
        date_to=date_to,
        min_price=min_price,
        max_price=max_price,
        page=page,
        page_size=page_size,
    )


@router.get("/my/registrations", summary="My registrations — upcoming/completed/cancelled")
def my_registrations(status: str | None = Query(None, description="Filter by registration status: confirmed|attended|cancelled"), db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    email = current_user.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Email not found in token")
    return my_registrations_service(db, email, status)


@router.post(
    "/templates",
    response_model=EventTemplateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Template",
    description="Create a template under tenant ownership. Frontend may send tenant_id from /tenant/me when auth token lacks tenant claim. enterprise_id optional.",
    responses={
        403: {"description": "Supplied tenant_id does not belong to authenticated user", "content": {"application/json": {"example": {"detail": "Supplied tenant_id does not belong to authenticated user"}}}},
    },
)
def create_template(payload: EventTemplateCreateRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return create_template_service(db, payload.model_dump(exclude_unset=True), current_user)


@router.get("/templates", response_model=list[EventTemplateResponse], summary="List Templates", description="List templates owned by authenticated tenant (tenant isolation).")
def list_templates(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return list_templates_service(db, current_user)


@router.get(
    "/templates/{template_id}",
    response_model=EventTemplateResponse,
    summary="Get Template by ID",
    responses={
        404: {"description": "Template not found", "content": {"application/json": {"example": {"detail": "Template not found"}}}},
        403: {"description": "Tenant mismatch", "content": {"application/json": {"example": {"detail": "Template does not belong to your tenant"}}}},
    },
)
def get_template(template_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return get_template_service(db, template_id, current_user)


@router.post(
    "/templates/{template_id}/apply",
    response_model=EventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Apply Template",
    description="Apply template to create a new Event (status=draft). Accepts optional tenant_id/enterprise_id; if only enterprise_id sent, tenant_id is derived as Enterprise.tenant_id. enterprise_id may stay null.",
    responses={
        404: {"description": "Template not found", "content": {"application/json": {"example": {"detail": "Template not found"}}}},
        403: {"description": "Tenant mismatch", "content": {"application/json": {"example": {"detail": "Template does not belong to your tenant"}}}},
        400: {"description": "Invalid template data", "content": {"application/json": {"example": {"detail": "Failed to create Event from template: ..."}}}},
    },
)
def apply_template(template_id: UUID, payload: EventTemplateApplyRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return apply_template_service(db, template_id, payload.model_dump(exclude_unset=True), current_user)


@router.get("/{event_id}", response_model=EventDetailResponse, status_code=status.HTTP_200_OK, summary="Get Event by ID")
def get_event(event_id: UUID = Path(..., description="Event ID"), db: Session = Depends(get_db)):
    return get_event_service(db, event_id)


@router.put("/{event_id}", response_model=EventResponse, status_code=status.HTTP_200_OK, summary="Update Event")
def update_event(event: EventUpdate, event_id: UUID = Path(..., description="Event ID"), db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return update_event_service(db, event_id, event, current_user)


@router.delete("/{event_id}", status_code=status.HTTP_200_OK, summary="Delete Event")
def delete_event(event_id: UUID = Path(..., description="Event ID"), db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    delete_event_service(db, event_id, current_user)
    return {"message": "Event deleted successfully"}


@router.post("/{event_id}/duplicate", response_model=EventResponse, status_code=status.HTTP_201_CREATED, summary="Duplicate Event")
def duplicate_event(event_id: UUID = Path(..., description="Event ID"), db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return duplicate_event_service(db, event_id, current_user)


@router.patch("/{event_id}/status", response_model=EventResponse, status_code=status.HTTP_200_OK, summary="Update Event Status")
def update_status(event_id: UUID, payload: EventStatusUpdate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from fastapi import HTTPException
    role = current_user.get("role")
    # TESTING: Enterprise Admin acts as Super Admin — approve / reject / request_changes
    _super_admin_only = {"approved", "rejected", "needs_revision"}
    if payload.status in _super_admin_only and role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail=f"Only Enterprise Admin can set status to '{payload.status}'.")
    return update_event_status_service(db, event_id, payload.status, current_user)


@router.post("/{event_id}/unpublish", response_model=EventResponse, status_code=status.HTTP_200_OK, summary="Unpublish Event")
def unpublish_event(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    if current_user.get("role") not in ("admin", "super_admin"):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Only Enterprise Admin can unpublish events (acting as Super Admin for testing).")
    return update_event_status_service(db, event_id, "approved", current_user)


@router.post("/{event_id}/archive", response_model=EventResponse, status_code=status.HTTP_200_OK, summary="Archive Event")
def archive_event(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return update_event_status_service(db, event_id, "archived", current_user)


@router.get("/{event_id}/admin-notes", summary="View Enterprise Admin Notes on Event")
def get_admin_notes(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    """Enterprise Admin / Provider can see the Enterprise Admin's latest reject/request-changes message (testing as Super Admin)."""
    from app.repository.event_repo import get_event_by_id
    from fastapi import HTTPException
    event = get_event_by_id(db, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return {
        "event_id": str(event.id),
        "title": event.title,
        "status": event.status,
        "last_admin_notes": event.last_admin_notes,
    }


@router.post("/{event_id}/resubmit", response_model=EventResponse, status_code=status.HTTP_200_OK, summary="Resubmit Event After Revision")
def resubmit_event(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    """Resubmit event for approval after Enterprise Admin requested changes (needs_revision -> pending_approval) — testing as Super Admin."""
    from app.repository.event_repo import get_event_by_id
    from fastapi import HTTPException
    event = get_event_by_id(db, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.status not in ("needs_revision", "draft"):
        raise HTTPException(status_code=400, detail=f"Cannot resubmit event in '{event.status}' status. Must be needs_revision or draft.")
    return update_event_status_service(db, event_id, "pending_approval", current_user)


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
    # IDOR check: participant can only cancel own, provider/admin any
    role = current_user.get("role")
    email = current_user.get("email")
    if role not in ["admin", "provider"] and reg.participant_email != email:
        raise HTTPException(status_code=403, detail="Not authorized to cancel this registration")
    if reg.status == "attended":
        raise HTTPException(status_code=400, detail="Cannot cancel: already attended (use refund flow)")
    if reg.status == "cancelled":
        return {"message": "Registration already cancelled"}
    reg.status = "cancelled"
    db.commit()
    try:
        from app.models.event_model import Event
        ev = db.query(Event).filter(Event.id == event_id).first()
        if ev:
            from app.services.notification_triggers import notify_single_cancellation
            notify_single_cancellation(db, ev, reg)
            # Auto-promote from waitlist if capacity has opened
            from app.services.event_service import _try_promote_from_waitlist
            try:
                _try_promote_from_waitlist(db, event_id, ev)
            except Exception:
                pass
    except Exception:
        pass
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


@router.post("/{event_id}/checkout", response_model=EventOrderResponse, status_code=status.HTTP_201_CREATED, summary="Checkout — Paid Registration")
def checkout(event_id: UUID, payload: EventCheckoutRequest, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return create_event_checkout_service(db, event_id, payload)


@router.get("/{event_id}/orders", summary="List Orders")
def list_orders(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return get_event_orders_service(db, event_id)


@router.post("/{event_id}/registrations/{reg_id}/refund", summary="Request Refund")
def refund_registration(event_id: UUID, reg_id: UUID, payload: EventRefundRequest | None = None, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return create_event_refund_service(db, event_id, reg_id, payload)


@router.post("/{event_id}/orders/{order_id}/refund", summary="Refund Order")
def refund_order(event_id: UUID, order_id: UUID, payload: EventRefundRequest | None = None, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return create_event_refund_service(db, event_id, order_id, payload)


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
    return check_in_service(db, event_id, payload, current_user)


@router.post("/{event_id}/uncheck-in", summary="Undo Check-in")
def uncheck_in(event_id: UUID, payload: EventUncheckInRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return uncheck_in_service(db, event_id, payload)


@router.post("/{event_id}/check-out", summary="Check-out Participant")
def check_out(event_id: UUID, payload: EventCheckOutRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return check_out_service(db, event_id, payload)


@router.post("/{event_id}/validate-qr", summary="Validate QR Code")
def validate_qr(event_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return validate_qr_service(db, event_id, payload.get("qr_code"))


@router.get("/{event_id}/registrations/{reg_id}/qr", summary="Get QR Code Image")
def get_qr_image(event_id: UUID, reg_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.models.event_aux_models import EventRegistration
    from fastapi import HTTPException
    from fastapi.responses import StreamingResponse
    import io

    reg = db.query(EventRegistration).filter(
        EventRegistration.id == reg_id,
        EventRegistration.event_id == event_id
    ).first()
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found")
    # IDOR: only owner or admin/provider can view QR
    role = current_user.get("role")
    email = current_user.get("email")
    if role not in ["admin", "provider"] and reg.participant_email != email:
        raise HTTPException(status_code=403, detail="Not authorized to view this QR code")

    try:
        import qrcode
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(reg.qr_code)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return StreamingResponse(buf, media_type="image/png", headers={"Content-Disposition": f"inline; filename=qr_{reg.qr_code}.png"})
    except ImportError:
        # Fallback: return QR code as text if qrcode not installed
        return {"qr_code": reg.qr_code, "message": "Install 'qrcode' package for image generation"}


@router.get("/{event_id}/calendar.ics", summary="Add to calendar — Event + Sessions (ICS)")
def event_calendar(event_id: UUID, db: Session = Depends(get_db)):
    from fastapi.responses import Response
    from app.services.calendar_service import event_to_ics
    from app.repository.event_repo import get_event_by_id
    from fastapi import HTTPException
    ev = get_event_by_id(db, event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    ics = event_to_ics(ev)
    return Response(content=ics, media_type="text/calendar", headers={"Content-Disposition": f"attachment; filename=event_{event_id}.ics"})


@router.get("/{event_id}/sessions/{session_id}/calendar.ics", summary="Add to calendar — Single Session (ICS)")
def session_calendar(event_id: UUID, session_id: str, db: Session = Depends(get_db)):
    from fastapi.responses import Response
    from app.services.calendar_service import event_to_ics
    from app.repository.event_repo import get_event_by_id
    from fastapi import HTTPException
    ev = get_event_by_id(db, event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    sess = next((s for s in (ev.sessions or []) if isinstance(s, dict) and s.get("id")==session_id), None)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    ics = event_to_ics(ev, sessions=[sess])
    return Response(content=ics, media_type="text/calendar", headers={"Content-Disposition": f"attachment; filename=event_{event_id}_session_{session_id}.ics"})


@router.get("/{event_id}/meeting-link", summary="Get Meeting Link (registered only)")
def get_meeting_link(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return get_meeting_link_service(db, event_id, current_user)


@router.post("/{event_id}/contact", summary="Contact Organiser")
def contact_organiser(event_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return contact_organiser_service(db, event_id, payload, current_user)


@router.get("/{event_id}/attendance", summary="Attendance Report")
def attendance(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return get_event_attendance_service(db, event_id)


# ---- Announcements, Feedback, Reviews, Reports, Templates (E13-E16) ----


@router.post("/{event_id}/announcements", status_code=status.HTTP_201_CREATED, summary="Send Announcement")
def announce(event_id: UUID, payload: EventAnnouncementCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return send_announcement_service(db, event_id, payload, current_user)


@router.post("/{event_id}/remind", status_code=status.HTTP_201_CREATED, summary="Send Event Reminder")
def remind(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.repository.event_repo import get_event_by_id
    from fastapi import HTTPException
    ev = get_event_by_id(db, event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    from app.services.notification_triggers import notify_event_reminder
    notify_event_reminder(db, ev)
    return {"message": "Reminders sent", "event_id": str(event_id)}


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
    return get_event_summary_service(db, enterprise_id)


@router.put(
    "/templates/{template_id}",
    response_model=EventTemplateResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Template",
    description="Partial update — send only `name` and/or `template_data`. `enterprise_id` cannot be changed after creation. Returns full EventTemplate.",
    responses={
        404: {"description": "Template not found", "content": {"application/json": {"example": {"detail": "Template not found"}}}},
        403: {"description": "Tenant mismatch", "content": {"application/json": {"example": {"detail": "Template does not belong to your tenant"}}}},
    },
)
def update_template(template_id: UUID, payload: EventTemplateUpdateRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.services.event_service import update_template_service
    return update_template_service(db, template_id, payload.model_dump(exclude_unset=True), current_user)


@router.delete(
    "/templates/{template_id}",
    response_model=EventTemplateDeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete Template",
    description="Delete a template. Empty body. Always succeeds if template exists and belongs to tenant; no 409 conflict (no usage check).",
    responses={
        404: {"description": "Template not found", "content": {"application/json": {"example": {"detail": "Template not found"}}}},
        403: {"description": "Tenant mismatch", "content": {"application/json": {"example": {"detail": "Template does not belong to your tenant"}}}},
    },
)
def delete_template(template_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.services.event_service import delete_template_service
    return delete_template_service(db, template_id, current_user)


@router.get(
    "/{event_id}/batch-check-in",
    response_model=list[EventBatchCheckInPreviewItem],
    summary="Batch Check-in — List registrations for scanning",
    description="Returns registrations for the event with eligibility computed server-side. Frontend renders table: pick multi + POST /batch-check-in + refresh attendance.",
)
def batch_check_in_preview(event_id: UUID, status_filter: str | None = Query(None, alias="status", description="Filter: confirmed|attended|cancelled"), db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.models.event_aux_models import EventRegistration
    from app.services.event_service import _get_event_or_404
    _get_event_or_404(db, event_id)
    q = db.query(EventRegistration).filter(EventRegistration.event_id == event_id)
    if status_filter:
        q = q.filter(EventRegistration.status == status_filter)
    regs = q.order_by(EventRegistration.participant_name).all()
    out: list[dict] = []
    for r in regs:
        can = r.status == "confirmed"
        if r.status == "attended":
            reason = "Already checked in"
        elif r.status == "cancelled":
            reason = "Cancelled — cannot check in"
        elif r.status == "no_show":
            reason = "Marked no-show"
        elif can:
            reason = "Ready to check in"
        else:
            reason = f"Status {r.status}"
        out.append(
            {
                "registration_id": r.id,
                "participant_name": r.participant_name,
                "participant_email": r.participant_email,
                "status": r.status,
                "qr_code": r.qr_code,
                "session_id": r.session_id,
                "ticket_type_id": r.ticket_type_id,
                "checked_in_at": r.checked_in_at.isoformat() if r.checked_in_at else None,
                "checked_out_at": r.checked_out_at.isoformat() if r.checked_out_at else None,
                "can_check_in": can,
                "eligibility_reason": reason,
            }
        )
    return out


@router.post(
    "/{event_id}/batch-check-in",
    response_model=EventBatchCheckInResponse,
    status_code=status.HTTP_200_OK,
    summary="Batch Check-in — Check in multiple participants",
    description="Batch check-in: body { participants: [{registration_id|qr_code, session_id?}] }. Returns {total, succeeded, failed, results:[{registration_id, participant_name, status, checked_in_at, message}]} — refresh GET /batch-check-in or GET /{id}/attendance after.",
)
def batch_check_in(event_id: UUID, payload: EventBatchCheckInRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.services.event_service import batch_checkin_service
    return batch_checkin_service(db, event_id, payload.participants)


@router.post("/auto-complete", summary="Auto-complete past published events", status_code=status.HTTP_200_OK)
def auto_complete_events(enterprise_id: UUID | None = None, db: Session = Depends(get_db), current_user: dict = Depends(get_current_admin)):
    from app.services.event_service import auto_complete_past_events_service
    return auto_complete_past_events_service(db, enterprise_id)





# ---- Event Order Status & Refund Approval ----

from app.schemas.event_schema import EventOrderStatusUpdate, EventRefundApproveRequest
from app.services.event_service import update_event_order_status_service, approve_event_refund_service


@router.patch(
    "/{event_id}/orders/{order_id}/status",
    summary="Update Event Order Status (Admin/Provider)",
)
def update_event_order_status(
    event_id: UUID,
    order_id: UUID,
    payload: EventOrderStatusUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_roles(["admin", "provider"])),
):
    return update_event_order_status_service(db, event_id, order_id, payload)


@router.post(
    "/{event_id}/orders/{order_id}/refund/approve",
    summary="Approve or Reject Event Refund (Admin/Provider)",
)
def approve_event_refund(
    event_id: UUID,
    order_id: UUID,
    payload: EventRefundApproveRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_roles(["admin", "provider"])),
):
    return approve_event_refund_service(db, event_id, order_id, payload)
