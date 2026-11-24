"""Records owned by another service, used in-process like the monolith's ORM objects (design D7, meetings).

The meeting code reads an Event / Training / TrainingLiveSession object and, when it creates or moves a meeting,
writes the link back into it (``event.meeting_link = ...``, a session's ``meeting_link``, ``live.meeting_link``).
In v2 those rows belong to events / learning. A ``RemoteRecord`` is built from the owner's snapshot; reading works
as before, and a change to a link field is recorded. The recorded writes then go to the owner:

* inside a hook call from the owner (``collecting()``): returned in the hook's response; the owner stores them in
  its own row and transaction, so the owner's response carries the new link exactly as before;
* otherwise (meetings' own endpoints, background retries): sent to the owner's ``meeting-writes`` endpoint right
  after meetings' own transaction commits (dropped on rollback).
"""
from __future__ import annotations

import contextvars
import copy
import logging
from types import SimpleNamespace

from sqlalchemy import event
from sqlalchemy.orm import Session

from mskit.replication import _restore

logger = logging.getLogger(__name__)
_LINK_FIELDS = {"meeting_link", "meeting_provider", "sessions"}
_collector: contextvars.ContextVar = contextvars.ContextVar("remote_writes", default=None)
_dirty: contextvars.ContextVar = contextvars.ContextVar("remote_dirty", default=None)


class RemoteRecord:
    """kind: "event" | "training" | "live_session"; owner: "events" | "learning"."""

    def __init__(self, kind: str, owner: str, snapshot: dict):
        data = {k: _restore(v) for k, v in snapshot.items() if not k.startswith("_")}
        object.__setattr__(self, "_kind", kind)
        object.__setattr__(self, "_owner", owner)
        object.__setattr__(self, "_original", copy.deepcopy({k: data.get(k) for k in _LINK_FIELDS}))
        tenant = _restore(snapshot.get("_enterprise_tenant_id"))
        object.__setattr__(self, "enterprise", SimpleNamespace(tenant_id=tenant) if tenant else None)
        for key, value in data.items():
            object.__setattr__(self, key, value)

    def __getattr__(self, name):  # an absent column reads as None, like an ORM attribute that was never set
        if name.startswith("__"):
            raise AttributeError(name)
        return None

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)
        if name in _LINK_FIELDS:
            dirty = _dirty.get()
            if dirty is None:
                dirty = []
                _dirty.set(dirty)
            if self not in dirty:
                dirty.append(self)

    # ---------------------------------------------------------------- the writes this record needs
    def writes(self) -> list[dict]:
        out = []
        if self._kind == "live_session":
            if (self.meeting_link, self.meeting_provider) != (self._original["meeting_link"], self._original["meeting_provider"]):
                out.append({"id": str(self.id), "link": self.meeting_link, "provider": self.meeting_provider})
            return out
        if (self.meeting_link, self.meeting_provider) != (self._original["meeting_link"], self._original["meeting_provider"]):
            out.append({"scope": "", "link": self.meeting_link, "provider": self.meeting_provider})
        before = {str(s.get("id")): s.get("meeting_link") for s in (self._original["sessions"] or [])}
        for s in self.sessions or []:
            sid = str(s.get("id"))
            if s.get("meeting_link") != before.get(sid):
                out.append({"scope": sid, "link": s.get("meeting_link")})
        return out

    def mark_sent(self) -> None:
        object.__setattr__(self, "_original", copy.deepcopy({k: getattr(self, k) for k in _LINK_FIELDS}))


def flag_modified(obj, key) -> None:
    """sqlalchemy's flag_modified for ORM objects; a RemoteRecord records the change itself."""
    if isinstance(obj, RemoteRecord):
        obj.__setattr__(key, getattr(obj, key))
        return
    from sqlalchemy.orm.attributes import flag_modified as _orm_flag_modified

    _orm_flag_modified(obj, key)


class collecting:
    """``with collecting() as writes:`` - link writes made inside are collected (hook calls) instead of sent."""

    def __enter__(self):
        self.writes: list[dict] = []
        self._token = _collector.set(self.writes)
        self._dirty_token = _dirty.set([])
        return self.writes

    def __exit__(self, *exc):
        for record in _dirty.get() or []:
            self.writes.extend(record.writes())
            record.mark_sent()
        _dirty.reset(self._dirty_token)
        _collector.reset(self._token)
        return False


def _send(record: RemoteRecord) -> None:
    from mskit.internal import call

    writes = record.writes()
    if not writes:
        return
    if record._kind == "event":
        call("events", "POST", f"/events/{record.id}/meeting-writes", json={"writes": writes})
    elif record._kind == "live_session":
        call("learning", "POST", f"/live-sessions/{record.id}/meeting-writes", json=writes[0])
    record.mark_sent()


@event.listens_for(Session, "after_commit")
def _flush_after_commit(session):
    if _collector.get() is not None:       # inside a hook: the caller returns the writes
        return
    dirty = _dirty.get() or []
    for record in list(dirty):
        try:
            _send(record)
        except Exception:  # noqa: BLE001 - the owner keeps its previous link; logged for an operator
            logger.exception("could not store the meeting link on %s %s", record._kind, getattr(record, "id", None))
    if dirty:
        _dirty.set([])


@event.listens_for(Session, "after_rollback")
def _drop_on_rollback(session):
    if _collector.get() is None:
        _dirty.set([])          # like the monolith: a rolled-back transaction leaves the owner's row unchanged
