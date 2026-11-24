"""Reads of events / trainings / live sessions owned by the events and learning services (design D7).

Each function replaces one query the monolith's meeting code ran on the shared database; records come back as
RemoteRecords (they behave like the ORM objects and record link writes), errors as the owner raised them.
"""
from __future__ import annotations

from datetime import datetime

from mskit.clients import _raise_passthrough
from mskit import internal
from mskit.remote_records import RemoteRecord


def _record(kind: str, owner: str, response):
    return RemoteRecord(kind, owner, response.json())


def event_staff_access(user: dict, access_token: str | None, event_id, *, write: bool) -> RemoteRecord:
    r = internal.call("events", "POST", f"/events/{event_id}/staff-access", json={"user": user, "access_token": access_token, "write": write}, expect=range(200, 600))
    _raise_passthrough(r)
    return _record("event", "events", r)


def training_staff_access(user: dict, access_token: str | None, training_id, *, write: bool) -> RemoteRecord:
    r = internal.call("learning", "POST", f"/trainings/{training_id}/staff-access", json={"user": user, "access_token": access_token, "write": write}, expect=range(200, 600))
    _raise_passthrough(r)
    return _record("training", "learning", r)


def public_event(event_id) -> RemoteRecord | None:
    r = internal.call("events", "GET", f"/events/{event_id}/public-snapshot")
    return None if r.status_code == 404 else _record("event", "events", r)


def event_by_id(event_id) -> RemoteRecord | None:
    r = internal.call("events", "GET", f"/events/{event_id}/snapshot")
    return None if r.status_code == 404 else _record("event", "events", r)


def public_training(training_id) -> RemoteRecord | None:
    r = internal.call("learning", "GET", f"/trainings/{training_id}/snapshot", params={"public": "true"})
    return None if r.status_code == 404 else _record("training", "learning", r)


def live_sessions(training_id) -> list[RemoteRecord]:
    return [RemoteRecord("live_session", "learning", s) for s in internal.call("learning", "GET", f"/trainings/{training_id}/live-sessions").json()]


def live_session(training_id, session_id) -> RemoteRecord:
    r = internal.call("learning", "GET", f"/trainings/{training_id}/live-sessions/{session_id}", expect=range(200, 600))
    _raise_passthrough(r)
    return _record("live_session", "learning", r)


def live_session_with_training(session_id) -> tuple[RemoteRecord | None, RemoteRecord | None]:
    r = internal.call("learning", "GET", f"/live-sessions/{session_id}")
    if r.status_code == 404:
        return None, None
    data = r.json()
    training = RemoteRecord("training", "learning", data["training"]) if data["training"] else None
    return RemoteRecord("live_session", "learning", data["live"]), training


def event_registered(event_id, email: str, statuses) -> bool:
    return internal.call("events", "GET", f"/events/{event_id}/registrations/exists", params={"email": email, "statuses": ",".join(statuses)}).json()["registered"]


def active_enrolment(training_id, email: str, statuses):
    """None, or an object with ``access_expires_at`` (like the TrainingEnrolment row)."""
    from types import SimpleNamespace

    data = internal.call("learning", "GET", f"/trainings/{training_id}/enrolments/active", params={"email": email, "statuses": ",".join(statuses)}).json()["enrolment"]
    if data is None:
        return None
    exp = data.get("access_expires_at")
    return SimpleNamespace(access_expires_at=datetime.fromisoformat(exp) if exp else None)


def participant_emails(target: dict, registration_statuses, enrolment_statuses) -> set:
    if "event_id" in target:
        r = internal.call("events", "GET", f"/events/{target['event_id']}/participant-emails", params={"statuses": ",".join(registration_statuses)})
    else:
        r = internal.call("learning", "GET", f"/trainings/{target['training_id']}/participant-emails", params={"statuses": ",".join(enrolment_statuses)})
    return set(r.json()["emails"])
