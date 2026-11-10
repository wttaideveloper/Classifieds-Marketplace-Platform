"""Event push + in-app notifications for the mobile app (same pattern as Trainings: `category` + ids in the push `data`).

Categories (the final enum; docs/event-notifications.md has the payloads and deep links):

    event_new                      event published                      tenant users (not the publisher)
    event_registration_confirmed   registration confirmed (free, paid)   the registrant
    event_registration_cancelled   registration cancelled                the registrant
    event_waitlist_joined          joined the waitlist                   the waitlisted person
    event_waitlist_promoted        a paid seat was offered (15 minutes)  the waitlisted person  (a free seat is simply a confirmed registration)
    event_reminder                 24h and 1h before the start           registered people
    event_updated                  date / time / venue / meeting link    registered people
    event_cancelled                the event was cancelled               registered and waitlisted people
    event_feedback_request         the event completed                   people who attended (and have not sent feedback)

Every push carries, as strings: category, event_id, event_name, entity_type = "event", entity_id = the event id, plus registration_id /
waitlist_id / starts_at where they apply. Values are never None, never nested (FCM data is a flat string map).

Delivery goes through the platform pipeline (create_automatic_notification: in-app feed, Socket.IO `notification`, FCM push), so the user's
push preference and registered devices are respected there. Each delivery is claimed first (notification_idempotency) so a retry or a second
worker never sends it twice. Everything runs on a background thread with its own database session and can never break the request that caused it.
Emails are unchanged: they stay in notification_triggers.

Not enforced: the notification preference's quiet hours. They are stored as bare HH:MM with no time zone, so there is nothing to compare them with.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func

from app.core.config import settings
from app.services import notification_idempotency

logger = logging.getLogger(__name__)

ACTIVE_REGISTRATION = ("confirmed", "attended")
SCHEDULE_FIELDS = ("start_date", "end_date", "venue", "meeting_link", "time_zone")
REMINDER_STATUS = "published"


def _dispatch(fn, *args, **kwargs) -> None:
    def runner():
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.exception("event notification failed")

    threading.Thread(target=runner, name="event-notification", daemon=True).start()


def _uuid(value) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except (ValueError, TypeError):
        return None


def starts_at_utc(event) -> str | None:
    """The event's start as UTC ISO text ("2026-11-20T09:00:00Z"). `start_date` is stored as the event's local time in `time_zone`."""
    from app.utils.event_utils import get_event_timezone

    start = getattr(event, "start_date", None)
    if start is None:
        return None
    aware = start if start.tzinfo else start.replace(tzinfo=get_event_timezone(event))
    return aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def payload(event_id, event_name, category: str, **extra) -> dict:
    """The push `data` for an event notification: flat, all strings, no None."""
    base = {"category": category, "event_id": event_id, "event_name": event_name, "entity_type": "event", "entity_id": event_id, **extra}
    return {k: str(v) for k, v in base.items() if v is not None and v != ""}


def _user_for(db, email: str | None, user_id=None) -> UUID | None:
    """The platform user for a registration or waitlist entry: the saved id, the same email on another registration, then the email lookup."""
    if _uuid(user_id):
        return _uuid(user_id)
    clean = (email or "").strip().lower()
    if "@" not in clean:
        return None
    from app.models.event_aux_models import EventRegistration

    row = (db.query(EventRegistration.user_id).filter(func.lower(EventRegistration.participant_email) == clean, EventRegistration.user_id.isnot(None))
           .order_by(EventRegistration.created_at.desc()).first())
    if row and row[0]:
        return row[0]
    from app.services.notification_triggers import _resolve_user_id_from_email

    return _resolve_user_id_from_email(db, clean)


def deliver(db, *, tenant_id, user_ids, category: str, title: str, body: str, data: dict, dedupe_key: str | None = None, collapse_key: str | None = None) -> bool:
    """One notification to one or more users. True when it was handed to the pipeline. Never raises."""
    users = [u for u in (_uuid(x) for x in user_ids) if u]
    if not users:
        logger.info("%s not delivered: no platform user for the recipient (%s)", category, dedupe_key)
        return False
    if dedupe_key and not notification_idempotency.claim(db, dedupe_key):
        return False
    meta = dict(data)
    if collapse_key:
        meta["collapse_key"] = collapse_key      # the push carries it as the Android collapse key / iOS thread id, not as data
    try:
        from app.services.notification_service import create_automatic_notification

        create_automatic_notification(db, title=title, message=body, category=category, user_ids=users, tenant_id=_uuid(tenant_id), metadata=meta, channels=["in_app", "push"])
        return True
    except Exception:
        logger.exception("%s delivery failed", category)
        try:
            db.rollback()
            if dedupe_key:
                notification_idempotency.release(db, dedupe_key)
        except Exception:
            logger.exception("could not release claim %s", dedupe_key)
        return False


def _session():
    from app.db.database import SessionLocal

    return SessionLocal()


def _facts(event) -> dict:
    return {"id": str(event.id), "name": getattr(event, "title", None) or "your event", "tenant_id": getattr(event, "tenant_id", None), "starts_at": starts_at_utc(event)}


def _person(row) -> dict:
    return {"id": str(row.id), "email": row.participant_email, "user_id": getattr(row, "user_id", None), "name": row.participant_name}


# ---- one registrant ----------------------------------------------------------------------------------------------------------

def _to_person(ev: dict, person: dict, *, category, title, body, extra, dedupe_key, collapse_key=None) -> bool:
    with _session() as db:
        user = _user_for(db, person["email"], person["user_id"])
        return deliver(db, tenant_id=ev["tenant_id"], user_ids=[user] if user else [], category=category, title=title, body=body,
                       data=payload(ev["id"], ev["name"], category, **extra), dedupe_key=dedupe_key, collapse_key=collapse_key)


def notify_registration_confirmed(event, reg) -> None:
    ev, person = _facts(event), _person(reg)
    _dispatch(_to_person, ev, person, category="event_registration_confirmed", title="Registration confirmed", body=f"You're registered for {ev['name']}",
              extra={"registration_id": person["id"]}, dedupe_key=f"event_registration_confirmed:{person['id']}")


def notify_registration_cancelled(event, reg) -> None:
    ev, person = _facts(event), _person(reg)
    _dispatch(_to_person, ev, person, category="event_registration_cancelled", title="Registration cancelled",
              body=f"Your registration for {ev['name']} was cancelled", extra={"registration_id": person["id"]},
              dedupe_key=f"event_registration_cancelled:{person['id']}")


def notify_waitlist_joined(event, entry) -> None:
    ev, person = _facts(event), _person(entry)
    _dispatch(_to_person, ev, person, category="event_waitlist_joined", title="Waitlist joined", body=f"You're on the waitlist for {ev['name']}",
              extra={"waitlist_id": person["id"]}, dedupe_key=f"event_waitlist_joined:{person['id']}")


def notify_waitlist_promoted(event, entry) -> None:
    """A paid seat was offered (the offer lasts 15 minutes, `expires_at`). A free seat is a confirmed registration instead (notify_registration_confirmed)."""
    ev, person = _facts(event), _person(entry)
    expires = getattr(entry, "payment_offer_expires_at", None)
    stamp = expires.replace(microsecond=0).isoformat() if expires else "offer"
    _dispatch(_to_person, ev, person, category="event_waitlist_promoted", title="Spot available", body=f"A spot opened for {ev['name']}. Register now",
              extra={"waitlist_id": person["id"], "expires_at": (expires.replace(microsecond=0).isoformat() + "Z") if expires else None},
              dedupe_key=f"event_waitlist_promoted:{person['id']}:{stamp}")


# ---- everyone registered -------------------------------------------------------------------------------------------------------

def _registered(db, event_id, statuses=ACTIVE_REGISTRATION) -> list[dict]:
    from app.models.event_aux_models import EventRegistration

    return [_person(r) for r in db.query(EventRegistration).filter(EventRegistration.event_id == event_id, EventRegistration.status.in_(statuses)).all()]


def _waiting(db, event_id) -> list[dict]:
    from app.models.event_aux_models import EventWaitlist

    return [_person(r) for r in db.query(EventWaitlist).filter(EventWaitlist.event_id == event_id, EventWaitlist.status.in_(("waiting", "payment_pending"))).all()]


def _fan_out(event_id: str, people_of, *, category, title, body_of, extra_of, key_of, collapse=False) -> int:
    from app.models.event_model import Event

    with _session() as db:
        event = db.get(Event, _uuid(event_id))
        if event is None:
            return 0
        ev = _facts(event)
        sent, seen = 0, set()
        for person in people_of(db, event.id):
            user = _user_for(db, person["email"], person["user_id"])
            if user is None or user in seen:        # one push per person, however many registrations they have
                continue
            seen.add(user)
            if deliver(db, tenant_id=ev["tenant_id"], user_ids=[user], category=category, title=title, body=body_of(ev), data=payload(ev["id"], ev["name"], category, **extra_of(ev, person)),
                       dedupe_key=key_of(ev, person, event), collapse_key=f"{category}:{ev['id']}" if collapse else None):
                sent += 1
        return sent


def notify_event_updated(event_id, changed_fields: list[str]) -> None:
    """Date, time, venue or meeting link changed (anything else, such as a title edit, is not pushed). Call after the change is committed."""
    fields = [f for f in changed_fields if f in SCHEDULE_FIELDS]
    if not fields:
        return
    stamp = datetime.utcnow().replace(microsecond=0).isoformat()
    _dispatch(_fan_out, str(event_id), _registered, category="event_updated", title="Event updated", body_of=lambda ev: f"Details changed for {ev['name']}",
              extra_of=lambda ev, p: {"registration_id": p["id"], "starts_at": ev["starts_at"], "changed": ",".join(sorted(fields))},
              key_of=lambda ev, p, e: f"event_updated:{p['id']}:{stamp}:{','.join(sorted(fields))}")


def notify_event_cancelled(event_id) -> None:
    """Registered and waitlisted people. Call after the cancellation is committed."""
    def people(db, eid):
        return _registered(db, eid) + _waiting(db, eid)

    _dispatch(_fan_out, str(event_id), people, category="event_cancelled", title="Event cancelled", body_of=lambda ev: f"{ev['name']} has been cancelled",
              extra_of=lambda ev, p: {}, key_of=lambda ev, p, e: f"event_cancelled:{ev['id']}:{p['id']}")


def notify_feedback_request(event_id) -> None:
    """People who attended and have not sent feedback yet. Call after the event completed."""
    def people(db, eid):
        from app.models.event_aux_models import EventFeedback

        done = {(f[0] or "").lower() for f in db.query(EventFeedback.participant_email).filter(EventFeedback.event_id == eid).all()}
        return [p for p in _registered(db, eid, ("attended",)) if (p["email"] or "").lower() not in done]

    _dispatch(_fan_out, str(event_id), people, category="event_feedback_request", title="Feedback", body_of=lambda ev: f"How was {ev['name']}? Share feedback",
              extra_of=lambda ev, p: {"registration_id": p["id"]}, key_of=lambda ev, p, e: f"event_feedback_request:{ev['id']}:{p['id']}")


def notify_manual_reminder(event_id) -> None:
    """The organiser's "send reminder" button: no once-only guard (they asked), but one active reminder per event on the phone."""
    _dispatch(_fan_out, str(event_id), _registered, category="event_reminder", title="Event reminder", body_of=lambda ev: f"{ev['name']} starts soon",
              extra_of=lambda ev, p: {"registration_id": p["id"], "starts_at": ev["starts_at"], "kind": "manual"},
              key_of=lambda ev, p, e: None, collapse=True)


# ---- a new event ------------------------------------------------------------------------------------------------------------------

def _fan_out_new(event_id: str, exclude_user_id, access_token) -> int:
    from app.models.event_model import Event
    from app.services.invigorate_auth_client import list_tenant_user_ids

    with _session() as db:
        event = db.get(Event, _uuid(event_id))
        if event is None or event.status != "published":
            return 0
        ev = _facts(event)
        tenant = _uuid(ev["tenant_id"])
        if tenant is None:
            logger.info("event_new skipped for %s: the event has no tenant", event_id)
            return 0
        exclude = _uuid(exclude_user_id)
        users = [u for u in (list_tenant_user_ids(tenant, access_token=access_token) if access_token else list_tenant_user_ids(tenant)) if u != exclude]
        if not users:
            logger.info("event_new for %s: no tenant users resolved (identity service configured?)", event_id)
            return 0
        if not notification_idempotency.claim(db, f"event_new:{ev['id']}"):      # once per event, however often it is republished
            return 0
        try:
            from app.services.notification_service import create_automatic_notification

            create_automatic_notification(db, title="New event", message=f"{ev['name']} is now open", category="event_new", user_ids=users, tenant_id=tenant,
                                          metadata=payload(ev["id"], ev["name"], "event_new"), channels=["in_app", "push"])
        except Exception:
            logger.exception("event_new delivery failed")
            db.rollback()
            notification_idempotency.release(db, f"event_new:{ev['id']}")
            return 0
        return len(users)


def notify_new_event(event_id, *, exclude_user_id=None, access_token: str | None = None) -> None:
    _dispatch(_fan_out_new, str(event_id), exclude_user_id, access_token)


# ---- scheduled reminders ------------------------------------------------------------------------------------------------------------

def reminder_hours() -> list[int]:
    """Hours before the start on which a reminder goes out, from EVENT_REMINDER_HOURS (empty = none), largest first."""
    hours = set()
    for part in str(settings.EVENT_REMINDER_HOURS or "").split(","):
        part = part.strip()
        if part.isdigit() and int(part) > 0:
            hours.add(int(part))
    return sorted(hours, reverse=True)


def due_reminder(remaining: timedelta, hours: list[int]) -> int | None:
    """Which reminder (in hours) is due when `remaining` is left before the start: the smallest step that is still ahead of us, so a
    late-published event gets one reminder and not one per step. None before the first step and after the start."""
    if remaining <= timedelta(0):
        return None
    for step in sorted(hours):
        if remaining <= timedelta(hours=step):
            return step
    return None


def send_due_event_reminders(db, now_utc: datetime | None = None) -> dict:
    """One scheduler tick. Each (registration, step, start time) is claimed before sending, so it goes out once however many workers run;
    a rescheduled event is reminded again for its new start."""
    from app.models.event_model import Event

    hours = reminder_hours()
    sent = {"events": 0, "sent": 0}
    if not hours:
        return sent
    now = now_utc or datetime.now(timezone.utc)
    naive = now.replace(tzinfo=None)
    events = db.query(Event).filter(
        Event.status == REMINDER_STATUS, Event.is_deleted.is_(False),
        Event.start_date.between(naive - timedelta(days=1), naive + timedelta(hours=max(hours) + 26)),   # +26h: any time zone offset
    ).all()
    for event in events:
        starts = starts_at_utc(event)
        if starts is None:
            continue
        step = due_reminder(datetime.strptime(starts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) - now, hours)
        if step is None:
            continue
        ev = _facts(event)
        sent["events"] += 1
        seen = set()
        for person in _registered(db, event.id):
            user = _user_for(db, person["email"], person["user_id"])
            if user is None or user in seen:
                continue
            seen.add(user)
            if deliver(db, tenant_id=ev["tenant_id"], user_ids=[user], category="event_reminder", title="Event reminder", body=f"{ev['name']} starts soon",
                       data=payload(ev["id"], ev["name"], "event_reminder", registration_id=person["id"], starts_at=ev["starts_at"], kind=f"{step}h"),
                       dedupe_key=f"event_reminder:{person['id']}:{step}h:{ev['starts_at']}", collapse_key=f"event_reminder:{ev['id']}"):
                sent["sent"] += 1
    return sent


def _scheduler_loop(interval_seconds: int) -> None:
    while True:
        try:
            with _session() as db:
                result = send_due_event_reminders(db)
            if result["sent"]:
                logger.info("event reminders sent: %s", result)
        except Exception:
            logger.exception("event reminder tick failed")
        time.sleep(interval_seconds)


def start_reminder_scheduler() -> threading.Thread | None:
    """Started from app startup, like the Training reminders (no Celery beat here). Running it in every worker is harmless: deliveries are claimed."""
    if not settings.event_reminders_enabled:
        return None
    thread = threading.Thread(target=_scheduler_loop, args=(settings.EVENT_REMINDER_INTERVAL_SECONDS,), name="event-reminders", daemon=True)
    thread.start()
    logger.info("Event reminder scheduler started (every %ss, hours before start: %s)", settings.EVENT_REMINDER_INTERVAL_SECONDS, reminder_hours())
    return thread
