from uuid import UUID

import sqlalchemy as sa
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.enterprise_model import Enterprise
from app.models.location_model import EnterpriseLocation
from app.repository.event_repo import (
    create_event,
    delete_event,
    get_event_by_id,
    get_events,
    update_event,
)
from app.repository.query_utils import build_pagination_meta
from app.schemas.event_schema import (
    EventDetailResponse,
    EventListItemResponse,
    EventPaginatedResponse,
    EventResponse,
)
from app.services.response_mappers import map_event_detail, map_event_list_item, map_event_write


def _validate_references(db: Session, enterprise_id: UUID, location_id: UUID | None, current_user: dict | None = None):
    enterprise = (
        db.query(Enterprise)
        .filter(Enterprise.id == enterprise_id, Enterprise.is_deleted.is_(False))
        .first()
    )
    if not enterprise:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Enterprise not found")
    # Tenant ownership: non-admin must own enterprise tenant
    if current_user and current_user.get("role") != "admin":
        user_tid = current_user.get("tenant_id")
        if user_tid and str(enterprise.tenant_id) != str(user_tid):
            raise HTTPException(status_code=403, detail="Not authorized for this enterprise/tenant")
    # Must be under approved business/profile
    if enterprise.status in ("draft", "pending", "inactive"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Enterprise not approved (status={enterprise.status}). Events can only be created under an approved business/profile.",
        )

    if location_id:
        location = (
            db.query(EnterpriseLocation)
            .filter(
                EnterpriseLocation.id == location_id,
                EnterpriseLocation.enterprise_id == enterprise_id,
                EnterpriseLocation.is_deleted.is_(False),
            )
            .first()
        )
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found for this enterprise"
            )


def _auto_meeting_link(delivery_mode: str | None, provider: str | None, existing: str | None) -> str | None:
    if existing:
        return existing
    if delivery_mode in ("online", "hybrid") and provider:
        import uuid as _u, random, string
        p = (provider or "").lower()
        if p == "zoom":
            return f"https://zoom.us/j/{random.randint(1000000000, 9999999999)}"
        if p == "google_meet":
            code = "".join(random.choices(string.ascii_lowercase, k=3)) + "-" + "".join(random.choices(string.ascii_lowercase, k=4)) + "-" + "".join(random.choices(string.ascii_lowercase, k=3))
            return f"https://meet.google.com/{code}"
        if p == "teams":
            return f"https://teams.microsoft.com/l/meetup-join/{_u.uuid4()}"
        return f"https://meet.example.com/{_u.uuid4()}"
    return existing

def _check_category(db: Session, category: str | None, subcategory: str | None):
    if not category and not subcategory:
        return
    from app.models.event_aux_models import EventCategory
    # If any categories exist, enforce that provided category must match an existing one
    count = db.query(EventCategory).count()
    if count == 0:
        return  # no admin categories yet, allow free-text
    if category:
        cat = db.query(EventCategory).filter(EventCategory.name == category).first()
        if not cat:
            raise HTTPException(status_code=400, detail=f"Category '{category}' not found in admin-managed categories")
    if subcategory and category:
        parent = db.query(EventCategory).filter(EventCategory.name == category).first()
        if parent:
            sub = db.query(EventCategory).filter(EventCategory.name == subcategory, EventCategory.parent_id == parent.id).first()
            if not sub:
                raise HTTPException(status_code=400, detail=f"Subcategory '{subcategory}' not found under '{category}'")

def _log_audit(db: Session, event_id: UUID, action: str, before: dict | None, after: dict | None, changed_by: str | None = None):
    try:
        from app.models.event_aux_models import EventAudit
        audit = EventAudit(event_id=event_id, action=action, before=before, after=after, changed_by=changed_by)
        db.add(audit); db.commit()
    except Exception:
        try: db.rollback()
        except: pass

def create_event_service(db: Session, event_data, current_user: dict | None = None):
    _validate_references(db, event_data.enterprise_id, event_data.location_id, current_user)
    _check_category(db, getattr(event_data, "category", None), getattr(event_data, "subcategory", None))
    # auto-create meeting link if needed
    if not event_data.meeting_link:
        event_data.meeting_link = _auto_meeting_link(event_data.delivery_mode, event_data.meeting_provider, None)
    created = create_event(db, event_data)
    _log_audit(db, created.id, "create", None, {"title": created.title, "status": created.status})
    return EventResponse.model_validate(map_event_write(created))


def get_events_service(
    db: Session,
    *,
    search: str | None = None,
    category: str | None = None,
    tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None,
    location_id: UUID | None = None,
    status_filter: str | None = None,
    delivery_mode: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_price: str | None = None,
    max_price: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> EventPaginatedResponse:
    items, total = get_events(
        db,
        search=search,
        category=category,
        tenant_id=tenant_id,
        enterprise_id=enterprise_id,
        location_id=location_id,
        status=status_filter,
        delivery_mode=delivery_mode,
        date_from=date_from,
        date_to=date_to,
        min_price=min_price,
        max_price=max_price,
        page=page,
        page_size=page_size,
    )
    return EventPaginatedResponse(
        items=[EventListItemResponse.model_validate(map_event_list_item(e)) for e in items],
        pagination=build_pagination_meta(total, page, page_size),
    )


def get_event_service(db: Session, event_id: UUID) -> EventDetailResponse:
    event = get_event_by_id(db, event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return EventDetailResponse.model_validate(map_event_detail(event))


def update_event_service(db: Session, event_id: UUID, update_data, current_user: dict = None):
    event = get_event_by_id(db, event_id, include_deleted=True)
    if not event or event.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    # Block status change via PUT — must use PATCH /status with proper guards
    if getattr(update_data, "status", None) is not None:
        raise HTTPException(status_code=400, detail="Status cannot be changed via PUT. Use PATCH /{event_id}/status.")

    location_id = update_data.location_id if update_data.location_id is not None else event.location_id
    _validate_references(db, event.enterprise_id, location_id, current_user)
    # category validation if provided
    if getattr(update_data, "category", None) is not None or getattr(update_data, "subcategory", None) is not None:
        _check_category(db, getattr(update_data, "category", None) or event.category, getattr(update_data, "subcategory", None))

    # auto-create meeting link on update if delivery_mode/provider changed and link missing
    cur_mode = update_data.delivery_mode if getattr(update_data, "delivery_mode", None) is not None else event.delivery_mode
    cur_provider = update_data.meeting_provider if getattr(update_data, "meeting_provider", None) is not None else event.meeting_provider
    cur_link = update_data.meeting_link if getattr(update_data, "meeting_link", None) is not None else event.meeting_link
    if not cur_link and cur_mode in ("online", "hybrid") and cur_provider:
        auto = _auto_meeting_link(cur_mode, cur_provider, None)
        if hasattr(update_data, "meeting_link"):
            try:
                update_data.meeting_link = auto
            except Exception:
                pass

    # capture old values for schedule change detection and audit
    old_vals = {k: getattr(event, k) for k in ["start_date","end_date","venue","meeting_link","time_zone","duration_type","category","subcategory","title","status"] if hasattr(event, k)}
    updated = update_event(db, event, update_data)
    _log_audit(db, event.id, "update", old_vals, {k: getattr(updated, k) for k in old_vals.keys()})
    # detect changes
    changes = {}
    for k, old in old_vals.items():
        new = getattr(updated, k, None)
        if old != new:
            changes[k] = f"{old} -> {new}"
    if changes:
        try:
            from app.services.notification_triggers import notify_schedule_change
            notify_schedule_change(db, updated, changes)
        except Exception:
            pass
    return EventResponse.model_validate(map_event_write(updated))


def delete_event_service(db: Session, event_id: UUID, current_user: dict = None):
    event = get_event_by_id(db, event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    _log_audit(db, event.id, "delete", {"status": event.status}, {"is_deleted": True})
    return delete_event(db, event)


def duplicate_event_service(db: Session, event_id: UUID, current_user: dict = None):
    import copy
    import uuid

    event = get_event_by_id(db, event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    # Clone fields, reset id/status
    payload = {c.key: getattr(event, c.key) for c in event.__table__.columns if c.key not in ("id", "created_at", "updated_at")}
    payload["status"] = "draft"
    payload["is_deleted"] = False
    # Deep copy sessions and regenerate ids so cloned event sessions are independent and addressable
    if payload.get("sessions"):
        cloned_sessions = copy.deepcopy(payload["sessions"])
        for s in cloned_sessions:
            s["id"] = str(uuid.uuid4())
            # normalize session_date to string if it's date object
            sd = s.get("session_date")
            if hasattr(sd, "isoformat"):
                s["session_date"] = sd.isoformat()
        payload["sessions"] = cloned_sessions

    from app.models.event_model import Event

    clone = Event(**payload)
    db.add(clone)
    db.commit()
    db.refresh(clone)
    return EventResponse.model_validate(map_event_write(clone))


def update_event_status_service(db: Session, event_id: UUID, new_status: str, current_user: dict = None):
    event = get_event_by_id(db, event_id, include_deleted=True)
    if not event or event.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    # 1) Provider cannot cancel — only Enterprise Admin may cancel
    if new_status == "cancelled" and current_user and current_user.get("role") == "provider":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Enterprise Admin can cancel events. Provider is assigned host only.",
        )

    # Lifecycle as per spec: pending_approval -> approved -> draft -> published -> completed -> archived
    # draft normally allows published, but restored drafts (requires_reapproval) must go via pending_approval
    VALID_TRANSITIONS = {
        "pending_approval": ["approved", "cancelled"],
        "approved": ["draft", "published", "cancelled", "archived"],
        "draft": ["published", "pending_approval", "cancelled", "archived"],
        "published": ["completed", "cancelled", "suspended", "approved", "archived"],
        "completed": ["archived"],
        "suspended": ["published", "cancelled", "archived"],
        "cancelled": ["draft", "archived"],
        "archived": [],
        "active": ["cancelled", "completed", "inactive"],
        "inactive": ["active", "cancelled"],
    }

    current_status = event.status
    allowed_next = VALID_TRANSITIONS.get(current_status, [])

    if new_status not in allowed_next:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot transition from '{current_status}' to '{new_status}'. Allowed: {allowed_next}"
        )

    # 2) Restored cancelled Event must be re-approved: cancelled→draft sets requires_reapproval,
    # then draft→published is blocked until pending_approval→approved clears it
    if event.requires_reapproval and current_status == "draft" and new_status == "published":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Event was previously cancelled and restored. Must go through draft → pending_approval → approved before publishing. Submit for approval first.",
        )

    # Set requires_reapproval when restoring cancelled → draft
    if event.status == "cancelled" and new_status == "draft":
        event.requires_reapproval = True

    # Clear requires_reapproval when Enterprise Admin approves (pending_approval → approved) — admin == Enterprise Admin (no Super Admin)
    if event.requires_reapproval and current_status == "pending_approval" and new_status == "approved":
        event.requires_reapproval = False

    previous = event.status
    event.status = new_status
    db.commit()
    db.refresh(event)
    _log_audit(db, event.id, "status_change", {"status": previous}, {"status": new_status})
    if new_status == "cancelled":
        try:
            from app.services.notification_triggers import notify_event_cancelled
            notify_event_cancelled(db, event, previous)
        except Exception:
            pass
    return EventResponse.model_validate(map_event_write(event))


# ---- auxiliary ----

def _get_event_or_404(db: Session, event_id: UUID):
    from app.repository.event_repo import get_event_by_id

    event = get_event_by_id(db, event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return event


def create_registration_service(db: Session, event_id: UUID, payload):
    from datetime import datetime
    event = _get_event_or_404(db, event_id)
    from app.models.event_aux_models import EventRegistration
    import uuid

    # Block cancelled/completed/archived/suspended events
    if event.status in ["cancelled", "completed", "archived", "suspended"]:
        raise HTTPException(status_code=400, detail=f"Registrations are closed — event is {event.status}")
    if event.status not in ["published"]:
        raise HTTPException(status_code=400, detail=f"Event not open for registration (status: {event.status})")

    now = datetime.utcnow()
    # Registration window enforcement
    if event.registration_open_at and now < event.registration_open_at:
        raise HTTPException(status_code=400, detail=f"Registration not yet open (opens {event.registration_open_at})")
    if event.registration_close_at and now > event.registration_close_at:
        raise HTTPException(status_code=400, detail=f"Registration closed (closed {event.registration_close_at})")
    if event.registration_cutoff and now > event.registration_cutoff:
        raise HTTPException(status_code=400, detail=f"Registration cutoff passed ({event.registration_cutoff})")

    # Group size handling
    group_size = getattr(payload, "group_size", None) or 1
    if group_size < 1:
        group_size = 1

    # Capacity enforcement (including group_size and per-ticket capacity) — with FOR UPDATE to prevent race
    need = group_size
    if event.capacity:
        try:
            max_capacity = int(event.capacity)
            current_count = db.query(EventRegistration).filter(
                EventRegistration.event_id == event_id,
                EventRegistration.status.in_(["confirmed", "attended"])
            ).with_for_update().count()
            if current_count + need > max_capacity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Event is at full capacity ({max_capacity} participants). Only {max_capacity - current_count} seats left. Please join waitlist."
                )
        except ValueError:
            pass
    if getattr(payload, "ticket_type_id", None) and event.ticket_types:
        for t in event.ticket_types or []:
            if isinstance(t, dict) and str(t.get("id")) == str(payload.ticket_type_id) and t.get("capacity"):
                try:
                    cap = int(t["capacity"])
                    cnt = db.query(EventRegistration).filter(EventRegistration.event_id==event_id, EventRegistration.ticket_type_id==payload.ticket_type_id, EventRegistration.status.in_(["confirmed","attended"])).with_for_update().count()
                    if cnt + need > cap:
                        raise HTTPException(status_code=400, detail=f"Ticket type at capacity ({cap})")
                except ValueError:
                    pass
    if event.max_participants:
        try:
            max_p = int(event.max_participants)
            current = db.query(EventRegistration).filter(EventRegistration.event_id==event_id, EventRegistration.status.in_(["confirmed","attended"])).with_for_update().count()
            if current + need > max_p:
                raise HTTPException(status_code=400, detail=f"Maximum participants reached ({max_p})")
        except ValueError:
            pass

    # Build custom_fields with group info and participant questions
    cf = dict(payload.custom_fields or {})
    if getattr(payload, "group_members", None):
        cf["group_members"] = payload.group_members
    if group_size > 1:
        cf["group_size"] = group_size

    reg = EventRegistration(
        event_id=event_id,
        participant_name=payload.participant_name,
        participant_email=payload.participant_email,
        custom_fields=cf,
        ticket_type_id=payload.ticket_type_id,
        status="confirmed",
        qr_code=str(uuid.uuid4())[:12].upper(),
    )
    db.add(reg)
    db.flush()
    # Group members: create additional registrations atomically in same transaction
    if getattr(payload, "group_members", None):
        for m in payload.group_members or []:
            try:
                name = m.get("name") or m.get("participant_name") or payload.participant_name
                email = m.get("email") or m.get("participant_email")
                if not email or email == payload.participant_email:
                    continue
                # prevent duplicate email per event
                exists = db.query(EventRegistration).filter(EventRegistration.event_id==event_id, EventRegistration.participant_email==email).first()
                if exists:
                    continue
                extra = EventRegistration(
                    event_id=event_id,
                    participant_name=name,
                    participant_email=email,
                    custom_fields={"group_leader": payload.participant_email},
                    ticket_type_id=payload.ticket_type_id,
                    status="confirmed",
                    qr_code=str(uuid.uuid4())[:12].upper(),
                )
                db.add(extra)
            except Exception:
                pass
    db.commit()
    db.refresh(reg)
    # best-effort confirmation notification (in_app, sync, no celery)
    try:
        from app.services.notification_triggers import notify_registration_confirmation
        notify_registration_confirmation(db, event, reg)
    except Exception:
        pass
    return reg


def get_event_registrations_service(db: Session, event_id: UUID):
    _get_event_or_404(db, event_id)
    from app.models.event_aux_models import EventRegistration

    return db.query(EventRegistration).filter(EventRegistration.event_id == event_id).all()


def create_waitlist_entry_service(db: Session, event_id: UUID, payload):
    _get_event_or_404(db, event_id)
    from app.models.event_aux_models import EventWaitlist

    entry = EventWaitlist(event_id=event_id, participant_name=payload.participant_name, participant_email=payload.participant_email)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def get_event_waitlist_service(db: Session, event_id: UUID):
    _get_event_or_404(db, event_id)
    from app.models.event_aux_models import EventWaitlist

    return db.query(EventWaitlist).filter(EventWaitlist.event_id == event_id).all()


def delete_waitlist_entry_service(db: Session, event_id: UUID, entry_id: UUID):
    from app.models.event_aux_models import EventWaitlist

    entry = db.query(EventWaitlist).filter(EventWaitlist.id == entry_id, EventWaitlist.event_id == event_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Waitlist entry not found")
    db.delete(entry)
    db.commit()
    return {"message": "Removed from waitlist"}


def get_sessions_service(db: Session, event_id: UUID):
    event = _get_event_or_404(db, event_id)
    sessions = event.sessions or []
    # Backfill missing ids for legacy embedded sessions created via POST /api/v1/events
    # These were persisted without id before fix, so PUT/DELETE would 404 - generate and persist now
    if sessions and any(not s.get("id") for s in sessions if isinstance(s, dict)):
        import copy
        import uuid

        from sqlalchemy.orm.attributes import flag_modified

        new_sessions = copy.deepcopy(sessions)
        changed = False
        for s in new_sessions:
            if isinstance(s, dict) and not s.get("id"):
                s["id"] = str(uuid.uuid4())
                changed = True
            # normalize date object to string for consistency
            sd = s.get("session_date") if isinstance(s, dict) else None
            if hasattr(sd, "isoformat"):
                s["session_date"] = sd.isoformat()
                changed = True
        if changed:
            # sort by (session_date, start_time) like add/update
            try:
                new_sessions = sorted(new_sessions, key=lambda x: (str(x.get("session_date") or ""), str(x.get("start_time") or "")))
            except Exception:
                pass
            event.sessions = new_sessions
            flag_modified(event, "sessions")
            db.commit()
            db.refresh(event)
            return event.sessions or []
    return sessions


def _validate_session_date(event, session_date):
    if session_date is None:
        return
    ev_start = event.start_date.date() if hasattr(event.start_date, "date") else event.start_date
    ev_end = event.end_date.date() if hasattr(event.end_date, "date") else event.end_date
    if not (ev_start <= session_date <= ev_end):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"session_date {session_date} must be within Event {ev_start}..{ev_end}",
        )


def _sort_sessions(sessions: list) -> list:
    def _key(s):
        sd = s.get("session_date") or ""
        # normalize date string for sorting
        st = s.get("start_time") or ""
        return (str(sd), str(st))

    return sorted(sessions, key=_key)


def add_session_service(db: Session, event_id: UUID, payload):
    import copy
    import uuid

    from sqlalchemy.orm.attributes import flag_modified

    event = _get_event_or_404(db, event_id)
    _validate_session_date(event, payload.session_date)
    sessions = copy.deepcopy(event.sessions or [])
    # model_dump will serialize date as date object; convert to ISO string for JSONB
    data = payload.model_dump()
    if data.get("session_date"):
        data["session_date"] = str(data["session_date"])
    new = {"id": str(uuid.uuid4()), **data}
    sessions.append(new)
    event.sessions = _sort_sessions(sessions)
    flag_modified(event, "sessions")
    db.commit()
    db.refresh(event)
    return new


def update_session_service(db: Session, event_id: UUID, session_id: str, payload):
    import copy

    from sqlalchemy.orm.attributes import flag_modified

    event = _get_event_or_404(db, event_id)
    sessions = copy.deepcopy(event.sessions or [])
    for s in sessions:
        if s.get("id") == session_id:
            updates = payload.model_dump(exclude_unset=True)
            if "session_date" in updates and updates["session_date"] is not None:
                _validate_session_date(event, updates["session_date"])
                updates["session_date"] = str(updates["session_date"])
            for k, v in updates.items():
                s[k] = v
            event.sessions = _sort_sessions(sessions)
            flag_modified(event, "sessions")
            db.commit()
            db.refresh(event)
            # return persisted version, not in-memory stale reference
            for updated in event.sessions:
                if updated.get("id") == session_id:
                    return updated
            return s
    raise HTTPException(status_code=404, detail="Session not found")


def delete_session_service(db: Session, event_id: UUID, session_id: str):
    from sqlalchemy.orm.attributes import flag_modified

    event = _get_event_or_404(db, event_id)
    original_len = len(event.sessions or [])
    sessions = [s for s in (event.sessions or []) if s.get("id") != session_id]
    if len(sessions) == original_len:
        raise HTTPException(status_code=404, detail="Session not found")
    event.sessions = sessions
    flag_modified(event, "sessions")
    db.commit()
    db.refresh(event)
    return {"message": "Session deleted"}


def get_event_attendance_service(db: Session, event_id: UUID):
    from app.models.event_aux_models import EventRegistration
    from app.schemas.event_schema import EventAttendanceItem, EventAttendanceResponse

    regs = db.query(EventRegistration).filter(EventRegistration.event_id == event_id).all()
    attended = [r for r in regs if r.status == "attended"]
    no_show = [r for r in regs if r.status == "confirmed"]

    # Build per-session attendance breakdown
    by_session: dict[str, dict] = {}
    for r in regs:
        if r.session_id:
            if r.session_id not in by_session:
                by_session[r.session_id] = {"total": 0, "attended": 0}
            by_session[r.session_id]["total"] += 1
            if r.status == "attended":
                by_session[r.session_id]["attended"] += 1

    participants = [
        EventAttendanceItem(
            registration_id=r.id,
            participant_name=r.participant_name,
            participant_email=r.participant_email,
            status=r.status,
            checked_in_at=r.checked_in_at.isoformat() if r.checked_in_at else None,
            checked_in_by=r.checked_in_by,
            checked_out_at=r.checked_out_at.isoformat() if r.checked_out_at else None,
            session_id=r.session_id,
            ticket_type_id=r.ticket_type_id
        )
        for r in regs
    ]

    return EventAttendanceResponse(
        event_id=event_id,
        total_registered=len(regs),
        total_attended=len(attended),
        total_no_show=len(no_show),
        attendance_by_session=by_session if by_session else None,
        participants=participants
    )


def send_announcement_service(db: Session, event_id: UUID, payload, current_user: dict | None = None):
    from app.models.event_aux_models import EventRegistration

    event = _get_event_or_404(db, event_id)
    message = payload.message if hasattr(payload, "message") else payload.get("message", "") if isinstance(payload, dict) else str(payload)
    title = payload.title if hasattr(payload, "title") and payload.title else f"Announcement: {event.title}"

    regs = db.query(EventRegistration).filter(
        EventRegistration.event_id == event_id,
        EventRegistration.status.in_(["confirmed", "attended"])
    ).all()
    recipient_count = len(regs)

    # Try to dispatch via notification service if available, otherwise just count
    try:
        from app.services.notification_service import _dispatch_notification
        # best-effort dispatch
        user_ids = [r.participant_email for r in regs]
        if user_ids and current_user:
            _dispatch_notification(
                db, current_user, title=title, message=message,
                notification_type="event_announcement", category="event",
                user_ids=user_ids, tenant_id=str(event.tenant_id) if event.tenant_id else None,
                metadata={"event_id": str(event_id)}
            )
    except Exception:
        pass

    return {"id": str(event_id), "event_id": str(event_id), "sent_by": current_user.get("id") if current_user else None,
            "recipient_count": recipient_count, "created_at": __import__("datetime").datetime.utcnow().isoformat(),
            "title": title, "message": message}


def create_feedback_service(db: Session, event_id: UUID, payload: dict, is_review: bool = False):
    _get_event_or_404(db, event_id)
    from app.models.event_aux_models import EventFeedback, EventRegistration
    # Verified review: only registered participants (confirmed/attended) can submit ratings/reviews — email required
    if is_review:
        email = payload.get("participant_email")
        if not email:
            raise HTTPException(status_code=400, detail="participant_email is required for verified reviews")
        reg = db.query(EventRegistration).filter(EventRegistration.event_id==event_id, EventRegistration.participant_email==email, EventRegistration.status.in_(["confirmed","attended"])).first()
        if not reg:
            raise HTTPException(status_code=403, detail="Only registered participants can submit verified reviews")
    fb = EventFeedback(
        event_id=event_id,
        participant_email=payload.get("participant_email"),
        form_id=payload.get("form_id"),
        answers=payload.get("answers"),
        rating=payload.get("rating"),
        comment=payload.get("comment"),
        is_review=is_review,
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)
    return fb


def get_event_feedbacks_service(db: Session, event_id: UUID, is_review: bool = False):
    _get_event_or_404(db, event_id)
    from app.models.event_aux_models import EventFeedback

    return db.query(EventFeedback).filter(EventFeedback.event_id == event_id, EventFeedback.is_review == is_review).all()


def moderate_review_service(db: Session, review_id: UUID, action: str):
    from app.models.event_aux_models import EventFeedback
    allowed = {"approved", "rejected", "pending"}
    if action not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid moderation action. Allowed: {sorted(allowed)}")
    fb = db.query(EventFeedback).filter(EventFeedback.id == review_id).first()
    if not fb:
        raise HTTPException(status_code=404, detail="Review not found")
    fb.moderation_status = action
    db.commit()
    return fb


def get_event_reports_service(db: Session, event_id: UUID, report_type: str):
    from app.models.event_aux_models import EventFeedback, EventRegistration

    event = _get_event_or_404(db, event_id)
    regs = db.query(EventRegistration).filter(EventRegistration.event_id == event_id).all()

    if report_type == "registration":
        by_status: dict = {}
        by_ticket: dict = {}
        for r in regs:
            by_status[r.status] = by_status.get(r.status, 0) + 1
            if r.ticket_type_id:
                by_ticket[r.ticket_type_id] = by_ticket.get(r.ticket_type_id, 0) + 1
        data = {"total_registrations": len(regs), "by_status": by_status, "by_ticket_type": by_ticket}
    elif report_type == "attendance":
        attended = sum(1 for r in regs if r.status == "attended")
        no_show = sum(1 for r in regs if r.status == "confirmed")
        cancelled = sum(1 for r in regs if r.status == "cancelled")
        by_session: dict = {}
        for r in regs:
            if r.session_id:
                by_session.setdefault(r.session_id, {"total": 0, "attended": 0})
                by_session[r.session_id]["total"] += 1
                if r.status == "attended":
                    by_session[r.session_id]["attended"] += 1
        data = {"total": len(regs), "attended": attended, "no_show": no_show, "cancelled": cancelled, "by_session": by_session}
    elif report_type == "feedback":
        feedbacks = db.query(EventFeedback).filter(EventFeedback.event_id == event_id, EventFeedback.is_review.is_(False)).all()
        reviews = db.query(EventFeedback).filter(EventFeedback.event_id == event_id, EventFeedback.is_review.is_(True)).all()
        ratings = [int(r.rating) for r in reviews if r.rating and str(r.rating).isdigit()]
        avg_rating = sum(ratings)/len(ratings) if ratings else None
        data = {"total_feedbacks": len(feedbacks), "total_reviews": len(reviews), "average_rating": avg_rating,
                "feedbacks": [{"id": str(f.id), "rating": f.rating, "comment": f.comment} for f in feedbacks[:20]]}
    elif report_type == "revenue":
        ticket_prices = {tt.get("id"): float(tt.get("price", 0) or 0) for tt in (event.ticket_types or []) if isinstance(tt, dict)}
        revenue_by_type: dict = {}
        total_revenue = 0
        for r in regs:
            if r.ticket_type_id and r.status in ("confirmed", "attended"):
                price = ticket_prices.get(r.ticket_type_id, 0)
                try:
                    price = float(price)
                except Exception:
                    price = 0
                revenue_by_type[r.ticket_type_id] = revenue_by_type.get(r.ticket_type_id, 0) + price
                total_revenue += price
        data = {"total_revenue": total_revenue, "by_ticket_type": revenue_by_type, "currency": event.currency}
    elif report_type == "cancellation":
        cancelled = [r for r in regs if r.status == "cancelled"]
        # also orders refund_requested
        try:
            from app.models.event_aux_models import EventOrder
            refunds = db.query(EventOrder).filter(EventOrder.event_id==event_id, EventOrder.status.in_(["refund_requested","refunded"])).all()
            by_reason = {}
            for o in refunds:
                by_reason[o.refund_reason or "unknown"] = by_reason.get(o.refund_reason or "unknown", 0) + 1
            data = {"total_cancelled": len(cancelled), "total_refunds": len(refunds), "by_reason": by_reason, "cancelled": [{"email": r.participant_email, "qr": r.qr_code} for r in cancelled[:20]]}
        except Exception:
            data = {"total_cancelled": len(cancelled), "cancelled": [{"email": r.participant_email} for r in cancelled[:20]]}
    elif report_type == "completion":
        total = len(regs)
        attended = sum(1 for r in regs if r.status=="attended")
        completion_rate = round(attended/total*100 if total else 0,2)
        data = {"total": total, "completed": attended, "completion_rate": completion_rate, "event_status": event.status, "is_completed": event.status=="completed"}
    else:
        by_status = {}
        for r in regs:
            by_status[r.status] = by_status.get(r.status, 0) + 1
        data = {"total_registrations": len(regs), "by_status": by_status}

    return {"event_id": str(event_id), "type": report_type, "data": data}


def get_event_summary_service(db: Session, enterprise_id: UUID | None = None):
    from sqlalchemy import func
    from app.models.event_aux_models import EventFeedback, EventRegistration
    from app.models.event_model import Event

    q = db.query(Event).filter(Event.is_deleted.is_(False))
    if enterprise_id:
        q = q.filter(Event.enterprise_id == enterprise_id)

    # by_status
    status_rows = q.with_entities(Event.status, func.count(Event.id)).group_by(Event.status).all()
    by_status = {row[0]: row[1] for row in status_rows}
    total = sum(by_status.values())

    # by_category
    cat_rows = q.with_entities(Event.category, func.count(Event.id)).group_by(Event.category).all()
    by_category = {row[0]: row[1] for row in cat_rows if row[0]}

    # by_delivery_mode
    del_rows = q.with_entities(Event.delivery_mode, func.count(Event.id)).group_by(Event.delivery_mode).all()
    by_delivery_mode = {row[0]: row[1] for row in del_rows if row[0]}

    from datetime import datetime
    now = datetime.utcnow()
    upcoming = q.filter(Event.start_date > now).count()
    past = q.filter(Event.start_date <= now).count()

    event_ids = [e.id for e in q.all()]
    reg_count = 0
    attended_count = 0
    avg_rating = None
    if event_ids:
        reg_count = db.query(func.count(EventRegistration.id)).filter(EventRegistration.event_id.in_(event_ids)).scalar() or 0
        attended_count = db.query(func.count(EventRegistration.id)).filter(EventRegistration.event_id.in_(event_ids), EventRegistration.status == "attended").scalar() or 0
        # rating is String, cast attempt
        try:
            avg_rating = db.query(func.avg(EventFeedback.rating.cast(sa.Float))).filter(EventFeedback.event_id.in_(event_ids), EventFeedback.is_review.is_(True)).scalar()
            if avg_rating is not None:
                avg_rating = float(avg_rating)
        except Exception:
            pass

    return {"total_events": total, "by_status": by_status, "by_category": by_category, "by_delivery_mode": by_delivery_mode,
            "upcoming_events": upcoming, "past_events": past, "total_registrations": reg_count, "total_attended": attended_count, "average_rating": avg_rating}


def create_template_service(db: Session, payload: dict):
    from app.models.event_aux_models import EventTemplate

    tmpl = EventTemplate(name=payload.get("name", "Template"), template_data=payload.get("template_data", payload))
    db.add(tmpl)
    db.commit()
    db.refresh(tmpl)
    return tmpl

# ---- Free/Paid: checkout / orders / refund ----

def _resolve_ticket(event, ticket_type_id: str | None):
    if not ticket_type_id:
        # free event or single price
        return {"price": event.price or "0", "currency": event.currency or "INR", "capacity": event.capacity}
    tickets = event.ticket_types or []
    for t in tickets:
        if isinstance(t, dict) and str(t.get("id")) == str(ticket_type_id):
            return t
    return None

def _ticket_effective_price(ticket: dict, event) -> str:
    from datetime import datetime
    now = datetime.utcnow()
    # early-bird
    eb_price = ticket.get("early_bird_price")
    eb_until = ticket.get("early_bird_until")
    if eb_price and eb_until:
        try:
            # eb_until may be string
            from datetime import datetime as _dt
            dt = _dt.fromisoformat(str(eb_until).replace("Z", ""))
            if now <= dt:
                return str(eb_price)
        except Exception:
            pass
    if ticket.get("promo_price"):
        return str(ticket["promo_price"])
    return str(ticket.get("price", event.price or "0"))

def create_event_checkout_service(db: Session, event_id: UUID, payload):
    from app.models.event_aux_models import EventOrder, EventRegistration
    event = _get_event_or_404(db, event_id)
    if event.status in ["cancelled", "completed", "archived", "suspended"]:
        raise HTTPException(status_code=400, detail=f"Checkout closed — event is {event.status}")
    if event.status not in ["published"]:
        raise HTTPException(status_code=400, detail=f"Event not open for checkout (status: {event.status})")
    # Registration window enforcement (same as free registration)
    from datetime import datetime
    now = datetime.utcnow()
    if event.registration_open_at and now < event.registration_open_at:
        raise HTTPException(status_code=400, detail=f"Registration not yet open (opens {event.registration_open_at})")
    if event.registration_close_at and now > event.registration_close_at:
        raise HTTPException(status_code=400, detail=f"Registration closed (closed {event.registration_close_at})")
    if event.registration_cutoff and now > event.registration_cutoff:
        raise HTTPException(status_code=400, detail=f"Registration cutoff passed ({event.registration_cutoff})")
    ticket = _resolve_ticket(event, payload.ticket_type_id)
    if payload.ticket_type_id and not ticket:
        raise HTTPException(status_code=404, detail="Ticket type not found")
    # capacity per ticket type
    if ticket and ticket.get("capacity"):
        try:
            cap = int(ticket["capacity"])
            cnt = db.query(EventOrder).filter(EventOrder.event_id==event_id, EventOrder.ticket_type_id==payload.ticket_type_id, EventOrder.status.in_(["confirmed"])).count()
            cnt += db.query(EventRegistration).filter(EventRegistration.event_id==event_id, EventRegistration.ticket_type_id==payload.ticket_type_id, EventRegistration.status.in_(["confirmed","attended"])).count()
            if cnt + payload.quantity > cap:
                raise HTTPException(status_code=400, detail=f"Ticket type at capacity ({cap})")
        except ValueError:
            pass
    price = _ticket_effective_price(ticket or {}, event)
    try:
        total = float(price) * payload.quantity
        amount = str(total)
    except Exception:
        amount = str(price)
    currency = ticket.get("currency", event.currency) if isinstance(ticket, dict) else (event.currency or "INR")
    # Free check
    is_free = (price == "0" or price == "0.0" or not price)
    payment_status = "confirmed" if is_free or price == "0" else "confirmed"  # stub: payment always confirmed (marketplace/merchant)
    order = EventOrder(
        event_id=event_id,
        participant_name=payload.participant_name,
        participant_email=payload.participant_email,
        ticket_type_id=payload.ticket_type_id,
        quantity=str(payload.quantity),
        amount=amount,
        currency=currency,
        payment_status=payment_status,
        status="confirmed",
        payment_provider=payload.payment_provider or "marketplace",
    )
    db.add(order); db.commit(); db.refresh(order)
    try:
        from app.services.notification_triggers import notify_payment_success
        notify_payment_success(db, event, order)
    except Exception:
        pass
    # also create registration for attendance tracking
    try:
        reg = EventRegistration(event_id=event_id, participant_name=payload.participant_name, participant_email=payload.participant_email, ticket_type_id=payload.ticket_type_id, status="confirmed", qr_code=str(__import__("uuid").uuid4())[:8].upper())
        db.add(reg); db.commit()
    except Exception:
        pass
    return order

def get_event_orders_service(db: Session, event_id: UUID):
    from app.models.event_aux_models import EventOrder
    _get_event_or_404(db, event_id)
    return db.query(EventOrder).filter(EventOrder.event_id==event_id).order_by(EventOrder.created_at.desc()).all()

def create_event_refund_service(db: Session, event_id: UUID, reg_id: UUID, payload):
    from app.models.event_aux_models import EventOrder, EventRegistration
    event = _get_event_or_404(db, event_id)
    # Try order first, then registration
    order = db.query(EventOrder).filter(EventOrder.event_id==event_id, EventOrder.id==reg_id).first()
    if order:
        if order.status in ("refunded",):
            raise HTTPException(status_code=400, detail="Already refunded")
        if order.payment_status == "refunded":
            raise HTTPException(status_code=400, detail="Already refunded")
        # if attended, no refund per spec
        # check registration attended
        reg = db.query(EventRegistration).filter(EventRegistration.event_id==event_id, EventRegistration.participant_email==order.participant_email, EventRegistration.status=="attended").first()
        if reg:
            raise HTTPException(status_code=400, detail="Cannot refund after attendance (checked-in)")
        order.status = "refund_requested"
        order.payment_status = "refund_requested"
        order.refund_reason = payload.reason if payload and getattr(payload, "reason", None) else None
        db.commit(); db.refresh(order)
        try:
            from app.services.notification_triggers import notify_refund_status
            notify_refund_status(db, event, order, "refund_requested")
        except Exception:
            pass
        return order
    # fallback: registration refund (free events)
    reg = db.query(EventRegistration).filter(EventRegistration.id==reg_id, EventRegistration.event_id==event_id).first()
    if not reg:
        raise HTTPException(status_code=404, detail="Registration/Order not found")
    if reg.status == "attended":
        raise HTTPException(status_code=400, detail="Cannot refund after attendance")
    reg.status = "cancelled"
    db.commit()
    try:
        from app.services.notification_triggers import notify_refund_status
        notify_refund_status(db, event, reg, "refund_requested")
    except Exception:
        pass
    return {"message": "Refund requested", "registration_id": str(reg.id), "status": reg.status}
