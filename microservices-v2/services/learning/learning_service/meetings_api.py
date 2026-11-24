"""What the meetings service needs from learning (design D7). /staff-access reproduces the monolith's
app/api/v1/endpoints/meeting.py `_owned_training` (checks and messages)."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from learning_service.db.database import get_db
from learning_service.models.training_model import Training, TrainingEnrolment, TrainingLiveSession
from mskit.app import require_internal_key
from mskit.clients import snapshot_of

router = APIRouter(dependencies=[Depends(require_internal_key)], tags=["internal"])


class StaffAccess(BaseModel):
    user: dict
    access_token: str | None = None
    write: bool = False


@router.post("/trainings/{training_id}/staff-access")
def staff_access(training_id: UUID, body: StaffAccess, db: Session = Depends(get_db)):
    from learning_service.repository.training_repo import require_training_owner

    user = body.user
    if user.get("role") not in ("admin", "provider", "super_admin"):
        raise HTTPException(status_code=403, detail="Only the business that owns the training can manage its meetings")
    training = require_training_owner(db, training_id, user, access_token=body.access_token)
    if body.write and user.get("role") == "super_admin":
        raise HTTPException(status_code=403, detail="A Super Admin can see a training's meetings but only the business can change them")
    return snapshot_of(training)


@router.get("/trainings/{training_id}/snapshot")
def training_snapshot(training_id: UUID, public: bool = False, db: Session = Depends(get_db)):
    if public:
        from learning_service.repository.training_repo import get_training_by_id

        training = get_training_by_id(db, training_id)
    else:
        training = db.get(Training, training_id)
    if training is None:
        raise HTTPException(404, "Training not found")
    return snapshot_of(training)


@router.get("/trainings/{training_id}/live-sessions")
def live_sessions(training_id: UUID, db: Session = Depends(get_db)):
    return [snapshot_of(s) for s in db.query(TrainingLiveSession).filter(TrainingLiveSession.training_id == training_id).all()]


@router.get("/trainings/{training_id}/live-sessions/{session_id}")
def live_session(training_id: UUID, session_id: UUID, db: Session = Depends(get_db)):
    live = db.query(TrainingLiveSession).filter(TrainingLiveSession.id == session_id, TrainingLiveSession.training_id == training_id).first()
    if live is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return snapshot_of(live)


@router.get("/live-sessions/{session_id}")
def live_session_with_training(session_id: UUID, db: Session = Depends(get_db)):
    live = db.get(TrainingLiveSession, session_id)
    if live is None:
        raise HTTPException(404, "Session not found")
    training = db.get(Training, live.training_id)
    return {"live": snapshot_of(live), "training": snapshot_of(training) if training is not None else None}


@router.get("/trainings/{training_id}/enrolments/active")
def active_enrolment(training_id: UUID, email: str, statuses: str = Query(...), db: Session = Depends(get_db)):
    row = db.query(TrainingEnrolment).filter(
        TrainingEnrolment.training_id == training_id, func.lower(TrainingEnrolment.participant_email) == email.strip().lower(),
        TrainingEnrolment.status.in_(statuses.split(","))).first()
    return {"enrolment": None if row is None else {"access_expires_at": row.access_expires_at.isoformat() if row.access_expires_at else None}}


@router.get("/trainings/{training_id}/participant-emails")
def participant_emails(training_id: UUID, statuses: str = Query(...), db: Session = Depends(get_db)):
    rows = db.query(func.lower(TrainingEnrolment.participant_email)).filter(
        TrainingEnrolment.training_id == training_id, TrainingEnrolment.status.in_(statuses.split(","))).all()
    return {"emails": sorted({e for (e,) in rows if e})}


class LiveWrite(BaseModel):
    id: str
    link: str | None = None
    provider: str | None = None


@router.post("/live-sessions/{session_id}/meeting-writes")
def live_session_writes(session_id: UUID, body: LiveWrite, db: Session = Depends(get_db)):
    live = db.get(TrainingLiveSession, session_id)
    if live is None:
        raise HTTPException(404, "Session not found")
    live.meeting_link = body.link
    if body.provider:
        live.meeting_provider = body.provider
    db.commit()
    return {"stored": 1}
