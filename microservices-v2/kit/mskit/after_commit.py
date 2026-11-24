"""Run a cross-service call only after the caller's transaction commits (dropped on rollback).

In the monolith, side writes such as the review moderation log were added to the caller's
transaction: they existed exactly when the main change committed. With separate databases the
side write is an internal API call; deferring it to after the commit keeps "only if the main
change committed". Not durable: if the process dies between the commit and the call, the call is
lost (logged as an error when it fails). A per-service outbox table is the durable upgrade.
"""
from __future__ import annotations

import logging

from sqlalchemy import event
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
_KEY = "mskit_after_commit"


def defer(db: Session, func, *args, **kwargs) -> None:
    if db is None:
        func(*args, **kwargs)
        return
    db.info.setdefault(_KEY, []).append((func, args, kwargs))


@event.listens_for(Session, "after_commit")
def _run(session):
    pending = session.info.pop(_KEY, [])
    for func, args, kwargs in pending:
        try:
            func(*args, **kwargs)
        except Exception:  # noqa: BLE001 - the main change is committed; report, never undo
            logger.exception("deferred cross-service call %s failed after commit", getattr(func, "__name__", func))


@event.listens_for(Session, "after_rollback")
def _drop(session):
    session.info.pop(_KEY, None)


@event.listens_for(Session, "after_soft_rollback")
def _drop_soft(session, previous_transaction):
    if previous_transaction.parent is None:
        session.info.pop(_KEY, None)
