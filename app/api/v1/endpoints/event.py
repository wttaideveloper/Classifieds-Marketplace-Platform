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
from app.schemas.event_schema import (
    EventAnnouncementCreate,
    EventCheckInRequest,
    EventCheckInResponse,
    EventCheckOutRequest,
    EventCheckOutResponse,
    EventCheckoutRequest,
    EventOrderResponse,
    EventQRValidateResponse,
    EventRefundRequest,
    EventRegistrationCreate,
    EventSessionCreate,
    EventSessionUpdate,
    EventUncheckInRequest,
    EventUncheckInResponse,
)
from app.services.event_service import (
    add_session_service,
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
    return create_event_service(db, event, current_user)


@router.get("/", response_model=EventPaginatedResponse, status_code=status.HTTP_200_OK, summary="List Events")
def list_events(
    search: str | None = Query(None, description="Search across title/description/category."),
    category: str | None = Query(None, description="Filter by category."),
    tenant_id: UUID | None = Query(None, description="Filter by tenant ID."),
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
    from app.models.event_aux_models import EventRegistration
    email = current_user.get("email")
    if not email:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Email not found in token")
    q = db.query(EventRegistration).filter(EventRegistration.participant_email==email)
    if status:
        q = q.filter(EventRegistration.status==status)
    regs = q.order_by(EventRegistration.created_at.desc()).all()
    from app.models.event_model import Event
    # Bulk fetch events to avoid N+1
    event_ids = [r.event_id for r in regs]
    ev_map = {e.id: e for e in db.query(Event).filter(Event.id.in_(event_ids)).all()} if event_ids else {}
    out = []
    for r in regs:
        ev = ev_map.get(r.event_id)
        out.append({"registration_id": str(r.id), "event_id": str(r.event_id), "event_title": ev.title if ev else None, "event_status": ev.status if ev else None, "event_start": ev.start_date.isoformat() if ev and ev.start_date else None, "registration_status": r.status, "qr_code": r.qr_code, "checked_in_at": r.checked_in_at.isoformat() if r.checked_in_at else None})
    return out


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
    # Only first approval needs admin; republishing/draft/archived/completed/suspended after approved don't need second approval
    if payload.status == "approved" and current_user.get("role") != "admin":
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Only admin can approve (pending_approval -> approved). Publish/draft/archived/completed/suspended can be done by provider after first approval.")
    return update_event_status_service(db, event_id, payload.status, current_user)


@router.post("/{event_id}/unpublish", response_model=EventResponse, status_code=status.HTTP_200_OK, summary="Unpublish Event")
def unpublish_event(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return update_event_status_service(db, event_id, "approved")


@router.post("/{event_id}/archive", response_model=EventResponse, status_code=status.HTTP_200_OK, summary="Archive Event")
def archive_event(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return update_event_status_service(db, event_id, "archived")


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
        from app.services.notification_triggers import notify_single_cancellation
        ev = db.query(Event).filter(Event.id == event_id).first()
        if ev:
            notify_single_cancellation(db, ev, reg)
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
    # Block check-in if event itself is cancelled/completed/archived/suspended
    from app.models.event_model import Event as Ev
    ev_chk = db.query(Ev).filter(Ev.id == event_id).first()
    if ev_chk and ev_chk.status in ["cancelled", "completed", "archived", "suspended"]:
        raise HTTPException(status_code=400, detail=f"Cannot check-in: event is {ev_chk.status}")
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


@router.post("/{event_id}/uncheck-in", summary="Undo Check-in")
def uncheck_in(event_id: UUID, payload: EventUncheckInRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.models.event_aux_models import EventRegistration
    from fastapi import HTTPException

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
    if reg.status != "attended":
        raise HTTPException(status_code=400, detail=f"Cannot undo: registration status is '{reg.status}', not 'attended'")

    reg.status = "confirmed"
    reg.checked_in_at = None
    reg.checked_in_by = None
    reg.session_id = None
    db.commit()
    db.refresh(reg)

    return EventUncheckInResponse(
        message="Check-in undone",
        registration_id=reg.id,
        participant_name=reg.participant_name,
        participant_email=reg.participant_email,
        status=reg.status,
        restored_to="confirmed"
    )


@router.post("/{event_id}/check-out", summary="Check-out Participant")
def check_out(event_id: UUID, payload: EventCheckOutRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.models.event_aux_models import EventRegistration
    from fastapi import HTTPException
    from datetime import datetime

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
        raise HTTPException(status_code=400, detail="Cannot check-out: registration is cancelled")
    if reg.status == "no_show":
        raise HTTPException(status_code=400, detail="Cannot check-out: registration is marked as no_show")
    if reg.checked_out_at:
        return EventCheckOutResponse(
            message="Already checked out",
            registration_id=reg.id,
            participant_name=reg.participant_name,
            participant_email=reg.participant_email,
            status=reg.status,
            checked_in_at=reg.checked_in_at.isoformat() if reg.checked_in_at else None,
            checked_out_at=reg.checked_out_at.isoformat() if reg.checked_out_at else None,
            session_id=reg.session_id
        )

    reg.checked_out_at = datetime.utcnow()
    db.commit()
    db.refresh(reg)

    return EventCheckOutResponse(
        message="Checked out successfully",
        registration_id=reg.id,
        participant_name=reg.participant_name,
        participant_email=reg.participant_email,
        status=reg.status,
        checked_in_at=reg.checked_in_at.isoformat() if reg.checked_in_at else None,
        checked_out_at=reg.checked_out_at.isoformat() if reg.checked_out_at else None,
        session_id=reg.session_id
    )


@router.post("/{event_id}/validate-qr", summary="Validate QR Code")
def validate_qr(event_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.models.event_aux_models import EventRegistration
    from fastapi import HTTPException

    qr_code = payload.get("qr_code")
    if not qr_code:
        raise HTTPException(status_code=400, detail="qr_code is required")

    reg = db.query(EventRegistration).filter(
        EventRegistration.qr_code == qr_code,
        EventRegistration.event_id == event_id
    ).first()

    if not reg:
        return EventQRValidateResponse(
            valid=False,
            message="QR code not found for this event"
        )

    from app.models.event_model import Event
    event = db.query(Event).filter(Event.id == event_id).first()

    return EventQRValidateResponse(
        valid=True,
        registration_id=reg.id,
        participant_name=reg.participant_name,
        participant_email=reg.participant_email,
        status=reg.status,
        event_id=event_id,
        event_title=event.title if event else None,
        ticket_type_id=reg.ticket_type_id,
        message=f"Valid ticket: {reg.participant_name} ({reg.status})"
    )


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
    from app.models.event_aux_models import EventRegistration
    from app.repository.event_repo import get_event_by_id
    from fastapi import HTTPException
    ev = get_event_by_id(db, event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    # allow if admin/provider or registered participant
    role = current_user.get("role")
    email = current_user.get("email")
    if role not in ("admin", "provider"):
        reg = db.query(EventRegistration).filter(EventRegistration.event_id==event_id, EventRegistration.participant_email==email, EventRegistration.status.in_(["confirmed","attended"])).first()
        if not reg:
            raise HTTPException(status_code=403, detail="Only registered participants can access meeting link")
    return {"event_id": str(event_id), "meeting_link": ev.meeting_link, "meeting_provider": ev.meeting_provider, "delivery_mode": ev.delivery_mode}


@router.post("/{event_id}/contact", summary="Contact Organiser")
def contact_organiser(event_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.repository.event_repo import get_event_by_id
    from fastapi import HTTPException
    ev = get_event_by_id(db, event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    msg = payload.get("message") or payload.get("text") or ""
    if not msg:
        raise HTTPException(status_code=400, detail="message is required")
    # create notification to organiser (best-effort via notification_triggers audit)
    try:
        from app.services.notification_triggers import _safe_notify
        _safe_notify(db, f"Message for {ev.title}", msg, "event_contact", ev.tenant_id, {"event_id": str(event_id), "from_email": current_user.get("email"), "from_name": current_user.get("name")}, participant_email=ev.organiser_contact)
    except Exception:
        pass
    return {"message": "Message sent to organiser", "event_id": str(event_id), "organiser": ev.organiser_contact}


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
