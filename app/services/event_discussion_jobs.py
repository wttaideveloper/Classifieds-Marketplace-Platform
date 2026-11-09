"""Background work for Event Discussions: judge messages the AI could not judge earlier, and the two nudges.

  unread reply      a published reply nobody opened is nudged once, EVENT_DISCUSSION_UNREAD_NUDGE_HOURS after it was published. Opening the message
                    (GET .../messages/{id}) ends it. One reminder per reply: no repeats.
  flagged messages  an event whose flagged messages have waited EVENT_DISCUSSION_PENDING_NUDGE_HOURS gets "You have N flagged messages waiting" to its
                    Enterprise Admins, again after the same wait, at most EVENT_DISCUSSION_PENDING_NUDGE_MAX times. It stops when the queue is empty.
Nudges are skipped during quiet hours (EVENT_DISCUSSION_QUIET_START_HOUR..END_HOUR, UTC) and tried again on the next round. Every job is safe to run twice.
"""
import logging
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.event_discussion_model import EventDiscussionMessage as Msg, EventDiscussionNudge
from app.models.event_model import Event
from app.services import event_discussion_notifications as notify
from app.services import event_discussion_service as svc

logger = logging.getLogger(__name__)
MAX_MODERATION_ATTEMPTS = 20


def jobs_enabled() -> bool:
    if settings.EVENT_DISCUSSION_JOBS_ENABLED is not None:
        return bool(settings.EVENT_DISCUSSION_JOBS_ENABLED)
    return settings.is_production


def in_quiet_hours(now: datetime) -> bool:
    start, end = settings.EVENT_DISCUSSION_QUIET_START_HOUR, settings.EVENT_DISCUSSION_QUIET_END_HOUR
    if start < 0 or end < 0 or start == end:
        return False
    return start <= now.hour < end if start < end else (now.hour >= start or now.hour < end)


def retry_moderation(db: Session, now: datetime | None = None) -> int:
    """Judges again the messages that were left unpublished because the AI was down."""
    now = now or datetime.utcnow()
    done = 0
    rows = db.query(Msg).filter(Msg.status == "pending_moderation", Msg.moderation_attempts < MAX_MODERATION_ATTEMPTS,
                                Msg.updated_at <= now - timedelta(seconds=30)).order_by(Msg.created_at).limit(100).all()
    for msg in rows:
        viewer = svc.Viewer(event=db.get(Event, msg.event_id), user_id=msg.author_id, name=msg.author_name, email=None, role=msg.author_role)
        events = svc._moderate(db, msg, viewer)
        msg.updated_at = now
        db.commit()
        svc._after(msg, events, None)
        done += 1 if msg.status != "pending_moderation" else 0
    return done


def nudge_unread_replies(db: Session, now: datetime | None = None) -> int:
    now = now or datetime.utcnow()
    if in_quiet_hours(now):
        return 0
    cutoff = now - timedelta(hours=settings.EVENT_DISCUSSION_UNREAD_NUDGE_HOURS)
    sent = 0
    for msg in db.query(Msg).filter(Msg.status == "published", Msg.parent_id.isnot(None), Msg.recipient_id.isnot(None), Msg.recipient_read_at.is_(None),
                                    Msg.nudged_at.is_(None), Msg.published_at <= cutoff).limit(200).all():
        event = db.get(Event, msg.event_id)
        if event is not None and notify.nudge_unread_reply(db, msg, event):
            msg.nudged_at = now
            sent += 1
        elif event is not None:
            msg.nudged_at = now                    # nobody to tell (no user id): do not retry forever
        db.commit()
    return sent


def nudge_pending_reviews(db: Session, now: datetime | None = None) -> int:
    now = now or datetime.utcnow()
    waiting_since = {}
    for event_id, oldest, count in db.query(Msg.event_id, func_min(Msg.created_at), func_count(Msg.id)).filter(Msg.status == "pending_review").group_by(Msg.event_id).all():
        waiting_since[event_id] = (oldest, count)
    for row in db.query(EventDiscussionNudge).all():          # the queue emptied: start counting afresh next time
        if row.event_id not in waiting_since:
            db.delete(row)
    sent = 0
    wait = timedelta(hours=settings.EVENT_DISCUSSION_PENDING_NUDGE_HOURS)
    for event_id, (oldest, count) in waiting_since.items():
        if in_quiet_hours(now):
            break
        state = db.get(EventDiscussionNudge, event_id)
        due_from = (state.last_sent_at if state else oldest) + wait
        if now < due_from or (state and state.sent_count >= settings.EVENT_DISCUSSION_PENDING_NUDGE_MAX):
            continue
        event = db.get(Event, event_id)
        number = (state.sent_count if state else 0) + 1
        if event is not None and notify.nudge_pending_review(db, event, count, number):
            if state is None:
                db.add(EventDiscussionNudge(event_id=event_id, last_sent_at=now, sent_count=1))
            else:
                state.last_sent_at, state.sent_count = now, number
            sent += 1
    db.commit()
    return sent


def func_min(column):
    from sqlalchemy import func

    return func.min(column)


def func_count(column):
    from sqlalchemy import func

    return func.count(column)


def run_all(db: Session, now: datetime | None = None) -> dict:
    return {"moderated": retry_moderation(db, now), "unread_nudges": nudge_unread_replies(db, now), "pending_nudges": nudge_pending_reviews(db, now)}


def _loop() -> None:
    from app.db.database import SessionLocal

    while True:
        time.sleep(max(30, settings.EVENT_DISCUSSION_JOBS_INTERVAL_SECONDS))
        try:
            with SessionLocal() as db:
                run_all(db)
        except Exception:
            logger.exception("event discussion jobs failed")


def start_scheduler():
    if not jobs_enabled():
        return None
    thread = threading.Thread(target=_loop, name="event-discussion-jobs", daemon=True)
    thread.start()
    return thread
