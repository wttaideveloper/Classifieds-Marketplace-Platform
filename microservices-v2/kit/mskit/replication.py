"""Read-only replicas of another service's reference tables (e.g. catalog's enterprises + locations).

Why: events, trainings, programs and bookings have foreign keys and ORM relationships to enterprises and
enterprise_locations, and their code reads ``event.enterprise.business_short_name`` etc. everywhere. Each
consumer keeps an identical, read-only copy of those tables in its own database, so that code and every
response stay unchanged. Only the owner (catalog) writes them.

How the copies stay current
1. Push on commit (freshness): the owner tracks changed rows of the replicated models in a session and,
   right after the commit, POSTs them to every subscriber's ``/internal/v1/replica/{topic}`` (2 s timeout,
   refused connections retried). This happens inside the request that made the change, so the client's
   next request (e.g. "create an event for the enterprise I just created") finds the row.
2. Full snapshot pull (durability): every ``REPLICA_SYNC_SECONDS`` (default 300) a subscriber fetches the
   owner's snapshot hash and, when it differs from its own, the full rows (``/internal/v1/replica/{topic}``)
   and applies them. Catches up after missed pushes, restarts and outages. The tables have no
   ``updated_at`` column (the schema is kept as is), hence snapshots rather than deltas.

Consistency: eventual, normally milliseconds (push), at worst one sync interval when a subscriber was down.
Writes to replicated tables anywhere but the owner are refused (ReplicaWriteError).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
_PENDING = "mskit_replica_pending"


class ReplicaWriteError(RuntimeError):
    pass


# ---------------------------------------------------------------- serialisation

def _jsonable(value):
    if isinstance(value, uuid.UUID):
        return {"__uuid__": str(value)}
    if isinstance(value, datetime):
        return {"__dt__": value.isoformat()}
    if isinstance(value, date):
        return {"__date__": value.isoformat()}
    if isinstance(value, Decimal):
        return {"__dec__": str(value)}
    return value


def _restore(value):
    if isinstance(value, dict) and len(value) == 1:
        (k, v), = value.items()
        if k == "__uuid__":
            return uuid.UUID(v)
        if k == "__dt__":
            return datetime.fromisoformat(v)
        if k == "__date__":
            return date.fromisoformat(v)
        if k == "__dec__":
            return Decimal(v)
    return value


def row_of(obj) -> dict:
    mapper = inspect(obj).mapper
    return {c.key: _jsonable(getattr(obj, c.key)) for c in mapper.column_attrs}


def snapshot_hash(rows: list[dict]) -> str:
    return hashlib.sha256(json.dumps(sorted(rows, key=lambda r: json.dumps(r, sort_keys=True, default=str)), sort_keys=True, default=str).encode()).hexdigest()


# ---------------------------------------------------------------- owner side

class Publisher:
    """Owner side. ``topics`` = {"catalog.enterprises": EnterpriseModel, ...}."""

    def __init__(self, topics: dict, subscribers_env: str = "REPLICA_SUBSCRIBERS"):
        self.topics = topics
        self.model_topic = {model: topic for topic, model in topics.items()}
        self.subscribers_env = subscribers_env

    def subscribers(self) -> list[str]:
        return [s.strip() for s in os.environ.get(self.subscribers_env, "").split(",") if s.strip()]

    def install(self) -> None:
        @event.listens_for(Session, "after_flush")
        def _collect(session, _ctx):
            for obj in list(session.new) + list(session.dirty) + list(session.deleted):
                topic = self.model_topic.get(type(obj))
                if topic:
                    deleted = obj in session.deleted
                    session.info.setdefault(_PENDING, {})[(topic, str(inspect(obj).identity or id(obj)))] = (topic, obj, deleted)

        tables = {model.__table__.name: topic for topic, model in self.topics.items()}

        @event.listens_for(Session, "do_orm_execute")
        def _bulk(state):
            # bulk UPDATE/DELETE (query.update(), delete()) bypass change tracking: push the whole table after the commit
            if state.is_update or state.is_delete:
                for mapper in state.all_mappers:
                    topic = tables.get(mapper.local_table.name)
                    if topic:
                        state.session.info.setdefault(_PENDING + "_full", set()).add(topic)

        @event.listens_for(Session, "after_commit")
        def _push(session):
            pending = session.info.pop(_PENDING, {})
            full_topics = session.info.pop(_PENDING + "_full", set())
            for topic in full_topics:
                self._push_full(topic, session)
            if not pending:
                return
            batches: dict[str, dict] = {}
            for topic, obj, deleted in pending.values():
                try:
                    row = row_of(obj)
                except Exception:  # noqa: BLE001 - expired/detached: the pull sync covers it
                    continue
                batches.setdefault(topic, {"upsert": [], "delete": []})["delete" if deleted else "upsert"].append(row)
            for topic, batch in batches.items():
                for service in self.subscribers():
                    try:
                        from mskit.internal import call

                        call(service, "POST", f"/replica/{topic}", json=batch, timeout=2.0)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("replica push %s -> %s failed (%s); the periodic sync will catch up", topic, service, exc)

        @event.listens_for(Session, "after_rollback")
        def _drop(session):
            session.info.pop(_PENDING, None)
            session.info.pop(_PENDING + "_full", None)

    def _push_full(self, topic: str, session) -> None:
        from sqlalchemy.orm import Session as _S

        model = self.topics[topic]
        with _S(bind=session.get_bind()) as fresh:
            rows = [row_of(o) for o in fresh.query(model).all()]
        for service in self.subscribers():
            try:
                from mskit.internal import call

                call(service, "POST", f"/replica/{topic}", json={"upsert": rows, "delete": [], "full": True}, timeout=10.0)
            except Exception as exc:  # noqa: BLE001
                logger.warning("full replica push %s -> %s failed (%s); the periodic sync will catch up", topic, service, exc)

    def snapshot(self, session_factory, topic: str) -> dict:
        model = self.topics[topic]
        with session_factory() as db:
            rows = [row_of(o) for o in db.query(model).all()]
        return {"hash": snapshot_hash(rows), "rows": rows}


# ---------------------------------------------------------------- subscriber side

class Subscriber:
    """Consumer side. ``topics`` = {"catalog.enterprises": LocalReplicaModel, ...} (same columns as the owner)."""

    def __init__(self, owner_service: str, topics: dict, session_factory):
        self.owner = owner_service
        self.topics = topics
        self.session_factory = session_factory
        self.models = set(topics.values())
        self._applying = threading.local()

    # replicas are read-only for the domain code
    def guard(self) -> None:
        models = self.models

        @event.listens_for(Session, "before_flush")
        def _refuse(session, _ctx, _instances):
            if getattr(self._applying, "on", False):
                return
            for obj in list(session.new) + list(session.dirty) + list(session.deleted):
                if type(obj) in models and (obj in session.new or obj in session.deleted or session.is_modified(obj)):
                    raise ReplicaWriteError(f"{type(obj).__name__} is a read-only replica; change it in the {self.owner} service")

    def apply(self, topic: str, upsert: list[dict], delete: list[dict] | None = None, *, full: bool = False) -> dict:
        model = self.topics[topic]
        pk = [c.key for c in inspect(model).primary_key]
        self._applying.on = True
        try:
            with self.session_factory() as db:
                seen = set()
                for raw in upsert:
                    data = {k: _restore(v) for k, v in raw.items()}
                    db.merge(model(**data))
                    seen.add(tuple(data[k] for k in pk))
                for raw in delete or []:
                    data = {k: _restore(v) for k, v in raw.items()}
                    obj = db.get(model, tuple(data[k] for k in pk) if len(pk) > 1 else data[pk[0]])
                    if obj is not None:
                        db.delete(obj)
                removed = 0
                if full:
                    for obj in db.query(model).all():
                        if tuple(getattr(obj, k) for k in pk) not in seen:
                            db.delete(obj)
                            removed += 1
                db.commit()
            return {"upserted": len(upsert), "deleted": len(delete or []) + removed}
        finally:
            self._applying.on = False

    def local_hash(self, topic: str) -> str:
        with self.session_factory() as db:
            return snapshot_hash([row_of(o) for o in db.query(self.topics[topic]).all()])

    def sync_once(self) -> dict:
        from mskit.internal import call

        report = {}
        for topic in self.topics:
            remote = call(self.owner, "GET", f"/replica/{topic}", params={"hash_only": "true"}).json()
            if remote["hash"] == self.local_hash(topic):
                report[topic] = "current"
                continue
            full = call(self.owner, "GET", f"/replica/{topic}", timeout=60).json()
            report[topic] = self.apply(topic, full["rows"], full=True)
        return report

    def start_background_sync(self) -> None:
        if os.environ.get("DISABLE_BACKGROUND_JOBS", "").lower() in ("1", "true", "yes"):
            return
        interval = float(os.environ.get("REPLICA_SYNC_SECONDS", "300"))

        def loop():
            while True:
                try:
                    self.sync_once()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("replica sync from %s failed: %s", self.owner, exc)
                time.sleep(interval)

        threading.Thread(target=loop, name=f"replica-sync-{self.owner}", daemon=True).start()


from pydantic import BaseModel  # noqa: E402


class ReplicaBatch(BaseModel):
    """Body of a push. Module level: with ``from __future__ import annotations`` FastAPI cannot resolve a
    model defined inside the router function and would read it as a query parameter."""

    upsert: list[dict] = []
    delete: list[dict] = []
    full: bool = False


def subscriber_router(subscriber: "Subscriber"):
    from fastapi import APIRouter, Depends

    from mskit.app import require_internal_key

    router = APIRouter(dependencies=[Depends(require_internal_key)], tags=["internal"])

    @router.post("/replica/{topic}")
    def receive(topic: str, batch: ReplicaBatch):
        if topic not in subscriber.topics:
            return {"ignored": topic}     # this service does not hold that replica
        return subscriber.apply(topic, batch.upsert, batch.delete, full=batch.full)

    return router


def publisher_router(publisher: Publisher, session_factory):
    from fastapi import APIRouter, Depends

    from mskit.app import require_internal_key

    router = APIRouter(dependencies=[Depends(require_internal_key)], tags=["internal"])

    @router.get("/replica/{topic}")
    def snapshot(topic: str, hash_only: bool = False):
        snap = publisher.snapshot(session_factory, topic)
        return {"hash": snap["hash"]} if hash_only else snap

    return router
