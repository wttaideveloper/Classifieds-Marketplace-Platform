"""Durable outbox (design D3) and idempotent receivers.

Sender side
  ``enqueue(db, service, method, path, payload, key)`` adds a row to the sender's own ``outbox_messages``
  table in the caller's transaction: the message exists exactly when the business change committed.
  Right after the commit the rows of that session are delivered (fast path, normally milliseconds);
  a relay thread retries what is still pending with exponential backoff (2 s, 4 s, ... capped at 1 h)
  and marks a message ``dead`` after ``OUTBOX_MAX_ATTEMPTS`` (default 50, about two days) for an
  operator to look at. Every delivery carries ``Idempotency-Key: <key>``.

Receiver side
  ``first_time(db, key)`` records the key in the receiver's ``processed_messages`` table in the same
  transaction as the effect; a duplicate delivery finds the key and does nothing (answers 200 again).

Both tables live in each service's **own new database**; the monolith's schema is unchanged.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy import JSON, Column, DateTime, Integer, String, Text, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
_SESSION_KEY = "mskit_outbox_pending"
_MODELS: dict = {}


def models_for(Base):
    """(OutboxMessage, ProcessedMessage) mapped on a service's own Base (created once per Base)."""
    if Base in _MODELS:
        return _MODELS[Base]

    class OutboxMessage(Base):
        __tablename__ = "outbox_messages"
        id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        idempotency_key = Column(String(255), nullable=False, unique=True)
        target_service = Column(String(50), nullable=False)
        method = Column(String(10), nullable=False)
        path = Column(String(500), nullable=False)
        payload = Column(JSON, nullable=True)
        status = Column(String(20), nullable=False, default="pending", index=True)  # pending | delivered | dead
        attempts = Column(Integer, nullable=False, default=0)
        next_attempt_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
        last_error = Column(Text, nullable=True)
        created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
        delivered_at = Column(DateTime, nullable=True)

    class ProcessedMessage(Base):
        __tablename__ = "processed_messages"
        idempotency_key = Column(String(255), primary_key=True)
        processed_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    _MODELS[Base] = (OutboxMessage, ProcessedMessage)
    return _MODELS[Base]


INFRA_TABLES = ["outbox_messages", "processed_messages"]


# ---------------------------------------------------------------- receiver

def first_time(db: Session, key: str, Base) -> bool:
    """True the first time ``key`` is seen (and records it in this transaction); False for a duplicate."""
    _, Processed = models_for(Base)
    if db.get(Processed, key) is not None:
        return False
    try:
        with db.begin_nested():
            db.add(Processed(idempotency_key=key))
        return True
    except IntegrityError:
        return False


def idempotency_key(request) -> str:
    from fastapi import HTTPException

    key = request.headers.get("idempotency-key")
    if not key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header required")
    return key[:255]


# ---------------------------------------------------------------- sender

class Outbox:
    def __init__(self, Base, session_factory):
        self.Message, _ = models_for(Base)
        self.session_factory = session_factory
        self.max_attempts = int(os.environ.get("OUTBOX_MAX_ATTEMPTS", "50"))

    def enqueue(self, db: Session, service: str, method: str, path: str, payload: dict | None, key: str) -> None:
        db.add(self.Message(idempotency_key=key[:255], target_service=service, method=method.upper(), path=path, payload=payload))
        db.info.setdefault(_SESSION_KEY, set()).add(self)   # deliver this outbox's rows right after the commit

    def _send(self, row) -> None:
        from mskit.internal import call

        call(row.target_service, row.method, row.path, json=row.payload, headers={"Idempotency-Key": row.idempotency_key},
             expect=(200, 201, 202, 204))

    def deliver_due(self, limit: int = 100) -> dict:
        now = datetime.utcnow()
        done = failed = 0
        with self.session_factory() as db:
            rows = (db.query(self.Message).filter(self.Message.status == "pending", self.Message.next_attempt_at <= now)
                    .order_by(self.Message.created_at).limit(limit).all())
            for row in rows:
                try:
                    self._send(row)
                    row.status, row.delivered_at, row.last_error = "delivered", datetime.utcnow(), None
                    done += 1
                except Exception as exc:  # noqa: BLE001
                    row.attempts += 1
                    row.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                    if row.attempts >= self.max_attempts:
                        row.status = "dead"
                        logger.error("outbox message %s to %s %s is dead after %d attempts: %s",
                                     row.idempotency_key, row.target_service, row.path, row.attempts, row.last_error)
                    else:
                        row.next_attempt_at = datetime.utcnow() + timedelta(seconds=min(2 ** row.attempts, 3600))
                    failed += 1
                db.commit()
        return {"delivered": done, "failed": failed}

    def pending_count(self) -> int:
        with self.session_factory() as db:
            return db.query(self.Message).filter(self.Message.status == "pending").count()

    def start_relay(self) -> None:
        if os.environ.get("DISABLE_BACKGROUND_JOBS", "").lower() in ("1", "true", "yes"):
            return
        interval = float(os.environ.get("OUTBOX_RELAY_SECONDS", "2"))

        def loop():
            while True:
                try:
                    self.deliver_due()
                except Exception:  # noqa: BLE001
                    logger.exception("outbox relay round failed")
                time.sleep(interval)

        threading.Thread(target=loop, name="outbox-relay", daemon=True).start()


# One listener for all outboxes: a session remembers which outboxes it wrote to.
@event.listens_for(Session, "after_commit")
def _deliver_now(session):
    for outbox in session.info.pop(_SESSION_KEY, set()):
        # runs after the commit, on a fresh session (never inside the caller's)
        try:
            outbox.deliver_due(limit=100)
        except Exception:  # noqa: BLE001 - the relay retries
            logger.exception("outbox fast-path delivery failed; the relay will retry")


@event.listens_for(Session, "after_rollback")
def _forget(session):
    session.info.pop(_SESSION_KEY, None)
