"""Meeting hooks of trainings (was app/services/meeting_service on_training_*): meetings does the provider work
and answers with the links to store; learning stores them on its own live-session rows. Never raises."""
from mskit.clients import meetings_call, snapshot_of


def _payload(training, lives):
    return {"record": snapshot_of(training), "live_sessions": [snapshot_of(live) for live in lives]}


def _lives(db, training):
    from learning_service.models.training_model import TrainingLiveSession

    return db.query(TrainingLiveSession).filter(TrainingLiveSession.training_id == training.id).all()


def _apply(db, writes, *, commit=True):
    from uuid import UUID

    from learning_service.models.training_model import TrainingLiveSession

    changed = False
    for w in writes:
        live = db.get(TrainingLiveSession, UUID(str(w["id"]))) if w.get("id") else None
        if live is None:
            continue
        live.meeting_link = w.get("link")
        if w.get("provider"):
            live.meeting_provider = w["provider"]
        changed = True
    if changed and commit:
        db.commit()


def on_training_status(db, training, previous, new_status) -> None:
    _apply(db, meetings_call("/hooks/trainings/status", {**_payload(training, _lives(db, training)), "previous": previous, "new_status": new_status}))


def on_training_deleted(db, training) -> None:
    # before the caller's delete commits: writes ride on the caller's commit
    _apply(db, meetings_call("/hooks/trainings/deleted", _payload(training, _lives(db, training))), commit=False)


def on_training_session_created(db, training, live) -> None:
    _apply(db, meetings_call("/hooks/trainings/session", {**_payload(training, [live]), "live_session_id": str(live.id)}))
