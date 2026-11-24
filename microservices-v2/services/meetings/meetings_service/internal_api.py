"""Internal API of the meetings service (design D7): hooks called by events and learning with a snapshot of the record.
The meeting code runs unchanged on RemoteRecords; the links it stores are returned (``writes``) and the caller stores
them in its own row and transaction."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from meetings_service.db.database import get_db
from meetings_service.services import meeting_service as svc
from mskit.app import require_internal_key
from mskit.remote_records import RemoteRecord, collecting

router = APIRouter(dependencies=[Depends(require_internal_key)], tags=["internal"])


class Hook(BaseModel):
    record: dict
    changed: list[str] = []
    previous: str | None = None
    new_status: str | None = None
    session_id: str | None = None
    deleted: bool = False
    live_sessions: list[dict] = []
    live_session_id: str | None = None


def _event(h: Hook):
    return RemoteRecord("event", "events", h.record)


def _training(h: Hook):
    return RemoteRecord("training", "learning", h.record)


@router.post("/hooks/events/updated")
def event_updated(h: Hook, db: Session = Depends(get_db)):
    with collecting() as writes:
        svc.on_event_updated(db, _event(h), set(h.changed))
    return {"writes": writes}


@router.post("/hooks/events/status")
def event_status(h: Hook, db: Session = Depends(get_db)):
    with collecting() as writes:
        svc.on_event_status(db, _event(h), h.previous, h.new_status)
    return {"writes": writes}


@router.post("/hooks/events/session")
def event_session(h: Hook, db: Session = Depends(get_db)):
    with collecting() as writes:
        svc.on_event_session_changed(db, _event(h), h.session_id, deleted=h.deleted)
    return {"writes": writes}


@router.post("/hooks/events/deleted")
def event_deleted(h: Hook, db: Session = Depends(get_db)):
    with collecting() as writes:
        svc.on_event_deleted(db, _event(h))
        db.commit()   # monolith: rode on the caller's commit of the delete
    return {"writes": writes}


@router.post("/hooks/trainings/status")
def training_status(h: Hook, db: Session = Depends(get_db)):
    with collecting() as writes:
        svc.on_training_status(db, _training(h), h.previous, h.new_status)
    return {"writes": writes}


@router.post("/hooks/trainings/deleted")
def training_deleted(h: Hook, db: Session = Depends(get_db)):
    with collecting() as writes:
        svc.on_training_deleted(db, _training(h))
        db.commit()
    return {"writes": writes}


@router.post("/hooks/trainings/session")
def training_session(h: Hook, db: Session = Depends(get_db)):
    live = RemoteRecord("live_session", "learning", h.live_sessions[0])
    with collecting() as writes:
        svc.on_training_session_created(db, _training(h), live)
    return {"writes": writes}


@router.get("/providers/placeholders-allowed")
def placeholders_allowed(provider: str = ""):
    return {"allowed": svc.placeholders_allowed(provider or None)}


class RecordBody(BaseModel):
    record: dict


@router.post("/records/is-generated-link")
def is_generated_link(body: RecordBody, db: Session = Depends(get_db)):
    return {"generated": svc.is_generated_link(db, RemoteRecord("event", "events", body.record))}
