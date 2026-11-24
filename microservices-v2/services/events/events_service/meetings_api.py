"""What the meetings service needs from events (design D7). The checks and messages of /staff-access are the
monolith's app/api/v1/endpoints/meeting.py `_owned_event`."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from events_service.db.database import get_db
from events_service.models.event_aux_models import EventRegistration
from events_service.repository.event_repo import build_event_viewer, get_event_by_id, viewer_owns_event
from mskit.app import require_internal_key
from mskit.clients import apply_meeting_writes, snapshot_of

router = APIRouter(dependencies=[Depends(require_internal_key)], tags=["internal"])


class StaffAccess(BaseModel):
    user: dict
    access_token: str | None = None
    write: bool = False


@router.post("/events/{event_id}/staff-access")
def staff_access(event_id: UUID, body: StaffAccess, db: Session = Depends(get_db)):
    user = body.user
    event = get_event_by_id(db, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    if user.get("role") not in ("admin", "provider", "super_admin"):
        raise HTTPException(status_code=403, detail="Only the business that owns the event can manage its meeting")
    viewer = build_event_viewer(db, user, access_token=body.access_token)
    if not viewer_owns_event(event, viewer):
        raise HTTPException(status_code=404, detail="Event not found")
    if body.write and user.get("role") == "super_admin":
        raise HTTPException(status_code=403, detail="A Super Admin can see an event's meeting but only the business can change it")
    return snapshot_of(event)


@router.get("/events/{event_id}/snapshot")
def snapshot(event_id: UUID, db: Session = Depends(get_db)):
    """The event as stored (soft-deleted included: the caller checks), like db.get(Event, id)."""
    from events_service.models.event_model import Event

    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "Event not found")
    return snapshot_of(event)


@router.get("/events/{event_id}/public-snapshot")
def public_snapshot(event_id: UUID, db: Session = Depends(get_db)):
    """get_event_by_id (not deleted), as the customer meeting endpoint used it."""
    event = get_event_by_id(db, event_id)
    if event is None:
        raise HTTPException(404, "Event not found")
    return snapshot_of(event)


@router.get("/events/{event_id}/registrations/exists")
def registration_exists(event_id: UUID, email: str, statuses: str = Query(...), db: Session = Depends(get_db)):
    found = db.query(EventRegistration.id).filter(
        EventRegistration.event_id == event_id, func.lower(EventRegistration.participant_email) == email.strip().lower(),
        EventRegistration.status.in_(statuses.split(","))).first()
    return {"registered": found is not None}


@router.get("/events/{event_id}/participant-emails")
def participant_emails(event_id: UUID, statuses: str = Query(...), db: Session = Depends(get_db)):
    rows = db.query(func.lower(EventRegistration.participant_email)).filter(
        EventRegistration.event_id == event_id, EventRegistration.status.in_(statuses.split(","))).all()
    return {"emails": sorted({e for (e,) in rows if e})}


class Writes(BaseModel):
    writes: list[dict]


@router.post("/events/{event_id}/meeting-writes")
def meeting_writes(event_id: UUID, body: Writes, db: Session = Depends(get_db)):
    """Links the meetings service generated outside a hook (its own endpoints, background retries)."""
    from events_service.models.event_model import Event

    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "Event not found")
    if apply_meeting_writes(event, body.writes):
        db.commit()
    return {"stored": len(body.writes)}
