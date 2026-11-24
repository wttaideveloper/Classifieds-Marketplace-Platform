"""Fill missing user ids on registrations / enrolments in their OWNER service (design D5).

The monolith wrote the found id straight into the row from the group-chat code. Those rows now belong to events /
learning: the owner runs the same lookup and saves it; its replica push (inside that call) updates this service's
read-only copy, and the rows are refreshed so the caller sees the ids exactly as before.
"""
from mskit import internal


def _refresh(db, rows):
    for row in rows:
        try:
            db.refresh(row)
        except Exception:  # noqa: BLE001 - a row the owner deleted meanwhile keeps its old values
            pass


def resolve_user_ids(db, rows, event, access_token=None) -> None:
    """event_registration_identity.resolve_user_ids, run by events."""
    missing = [r for r in rows if not r.user_id]
    if not missing:
        return
    internal.call("events", "POST", f"/events/{event.id}/registrations/resolve-user-ids",
                  json={"registration_ids": [str(r.id) for r in missing], "access_token": access_token})
    _refresh(db, missing)


def fill_training_enrolments(db, rows, training, access_token=None) -> None:
    missing = [r for r in rows if not r.user_id]
    if not missing:
        return
    internal.call("learning", "POST", f"/trainings/{training.id}/enrolments/resolve-user-ids",
                  json={"enrolment_ids": [str(r.id) for r in missing], "access_token": access_token})
    _refresh(db, missing)
