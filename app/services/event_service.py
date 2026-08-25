from uuid import UUID

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


def _validate_references(db: Session, enterprise_id: UUID, location_id: UUID | None):
    enterprise = (
        db.query(Enterprise)
        .filter(Enterprise.id == enterprise_id, Enterprise.is_deleted.is_(False))
        .first()
    )
    if not enterprise:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Enterprise not found")

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


def create_event_service(db: Session, event_data):
    _validate_references(db, event_data.enterprise_id, event_data.location_id)
    return EventResponse.model_validate(map_event_write(create_event(db, event_data)))


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


def update_event_service(db: Session, event_id: UUID, update_data):
    event = get_event_by_id(db, event_id, include_deleted=True)
    if not event or event.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    location_id = update_data.location_id if update_data.location_id is not None else event.location_id
    _validate_references(db, event.enterprise_id, location_id)

    return EventResponse.model_validate(map_event_write(update_event(db, event, update_data)))


def delete_event_service(db: Session, event_id: UUID):
    event = get_event_by_id(db, event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return delete_event(db, event)


def duplicate_event_service(db: Session, event_id: UUID):
    event = get_event_by_id(db, event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    # Clone fields, reset id/status
    payload = {c.key: getattr(event, c.key) for c in event.__table__.columns if c.key not in ("id", "created_at", "updated_at")}
    payload["status"] = "draft"
    payload["is_deleted"] = False

    from app.models.event_model import Event

    clone = Event(**payload)
    db.add(clone)
    db.commit()
    db.refresh(clone)
    return EventResponse.model_validate(map_event_write(clone))


def update_event_status_service(db: Session, event_id: UUID, status: str):
    event = get_event_by_id(db, event_id, include_deleted=True)
    if not event or event.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    event.status = status
    db.commit()
    db.refresh(event)
    return EventResponse.model_validate(map_event_write(event))


# ---- auxiliary ----

def _get_event_or_404(db: Session, event_id: UUID):
    from app.repository.event_repo import get_event_by_id

    event = get_event_by_id(db, event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return event


def create_registration_service(db: Session, event_id: UUID, payload):
    _get_event_or_404(db, event_id)
    from app.models.event_aux_models import EventRegistration
    import uuid

    reg = EventRegistration(
        event_id=event_id,
        participant_name=payload.participant_name,
        participant_email=payload.participant_email,
        custom_fields=payload.custom_fields or {},
        ticket_type_id=payload.ticket_type_id,
        status="confirmed",
        qr_code=str(uuid.uuid4())[:8].upper(),
    )
    db.add(reg)
    db.commit()
    db.refresh(reg)
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
    return event.sessions or []


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
    import uuid

    event = _get_event_or_404(db, event_id)
    _validate_session_date(event, payload.session_date)
    sessions = list(event.sessions or [])
    # model_dump will serialize date as date object; convert to ISO string for JSONB
    data = payload.model_dump()
    if data.get("session_date"):
        data["session_date"] = str(data["session_date"])
    new = {"id": str(uuid.uuid4()), **data}
    sessions.append(new)
    event.sessions = _sort_sessions(sessions)
    db.commit()
    db.refresh(event)
    return new


def update_session_service(db: Session, event_id: UUID, session_id: str, payload):
    event = _get_event_or_404(db, event_id)
    sessions = list(event.sessions or [])
    for s in sessions:
        if s.get("id") == session_id:
            updates = payload.model_dump(exclude_unset=True)
            if "session_date" in updates and updates["session_date"] is not None:
                _validate_session_date(event, updates["session_date"])
                updates["session_date"] = str(updates["session_date"])
            for k, v in updates.items():
                s[k] = v
            event.sessions = _sort_sessions(sessions)
            db.commit()
            return s
    raise HTTPException(status_code=404, detail="Session not found")


def delete_session_service(db: Session, event_id: UUID, session_id: str):
    event = _get_event_or_404(db, event_id)
    sessions = [s for s in (event.sessions or []) if s.get("id") != session_id]
    event.sessions = sessions
    db.commit()
    return {"message": "Session deleted"}


def get_event_attendance_service(db: Session, event_id: UUID):
    from app.models.event_aux_models import EventRegistration

    regs = db.query(EventRegistration).filter(EventRegistration.event_id == event_id).all()
    return {"total": len(regs), "attended": len([r for r in regs if r.status == "attended"]), "no_show": len([r for r in regs if r.status == "confirmed"])}


def send_announcement_service(db: Session, event_id: UUID, message: str):
    _get_event_or_404(db, event_id)
    # Stub: would fan-out via notification service
    return {"message": "Announcement queued", "recipients": 0}


def create_feedback_service(db: Session, event_id: UUID, payload: dict, is_review: bool = False):
    _get_event_or_404(db, event_id)
    from app.models.event_aux_models import EventFeedback

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

    fb = db.query(EventFeedback).filter(EventFeedback.id == review_id).first()
    if not fb:
        raise HTTPException(status_code=404, detail="Review not found")
    fb.moderation_status = action
    db.commit()
    return fb


def get_event_reports_service(db: Session, event_id: UUID, report_type: str):
    _get_event_or_404(db, event_id)
    return {"event_id": str(event_id), "type": report_type, "data": {}}


def create_template_service(db: Session, payload: dict):
    from app.models.event_aux_models import EventTemplate

    tmpl = EventTemplate(name=payload.get("name", "Template"), template_data=payload.get("template_data", payload))
    db.add(tmpl)
    db.commit()
    db.refresh(tmpl)
    return tmpl
