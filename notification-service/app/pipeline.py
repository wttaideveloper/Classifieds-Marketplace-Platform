"""From an event to a notification to a delivery, and the nudges that follow events.

ingest()   an event arrives: one notification per person (the inbox), and one delivery per extra channel the person allows.
deliver()  the worker sends due deliveries, retrying failures with a growing wait, and gives up after MAX_ATTEMPTS.
nudges     "when A happens and B does not follow within N minutes, send C": see on_event() and run_nudges().
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Delivery, Device, EventType, Notification, NudgeJob, NudgeRule, Preference
from app.providers import Providers

logger = logging.getLogger(__name__)

EXTRA_CHANNELS = ("push", "email")
ALL_CHANNELS = ("in_app",) + EXTRA_CHANNELS


class UnknownEvent(Exception):
    pass


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def render(template: str, data: dict) -> str:
    """Fills {name} from data. A missing name stays as it is and a broken template is returned unchanged: never an error."""
    try:
        return template.format_map(_Safe({k: v for k, v in (data or {}).items()}))
    except (ValueError, IndexError, KeyError, AttributeError):
        return template


def _allowed(db: Session, user_id: str, category: str, channel: str) -> bool:
    row = db.query(Preference).filter(Preference.user_id == user_id, Preference.category == category, Preference.channel == channel).first()
    return True if row is None else bool(row.enabled)


def ingest(db: Session, *, source: str, event: str, recipients: list[dict], data: dict, tenant_id: str | None = None,
           idempotency_key: str | None = None, channels: list[str] | None = None, title: str | None = None, body: str | None = None) -> list[dict]:
    """Returns one result per recipient: {user_id, status: created | duplicate, notification_id, channels}."""
    kind = db.get(EventType, event)
    if (kind is None or not kind.active) and not title:
        raise UnknownEvent(event)
    category = kind.category if kind else "general"
    priority = kind.priority if kind else "normal"
    wanted = [c for c in (channels or (kind.channels if kind else ["in_app"])) if c in ALL_CHANNELS]
    results = []
    for person in recipients:
        user_id = str(person["user_id"])
        values = {**(data or {}), "user_id": user_id}
        note = Notification(
            source=source, idempotency_key=idempotency_key, user_id=user_id, tenant_id=tenant_id, event=event, category=category,
            title=(title or render(kind.title, values))[:255], body=body if body is not None else render(kind.body if kind else "", values),
            data=data or {}, priority=priority,
        )
        try:
            with db.begin_nested():
                db.add(note)
                db.flush()
        except IntegrityError:
            existing = db.query(Notification).filter_by(source=source, idempotency_key=idempotency_key, user_id=user_id).first()
            results.append({"user_id": user_id, "status": "duplicate", "notification_id": existing.id if existing else None, "channels": []})
            continue
        used = ["in_app"]
        for channel in wanted:
            if channel not in EXTRA_CHANNELS:
                continue
            if priority != "critical" and not _allowed(db, user_id, category, channel):
                continue
            email = person.get("email") if channel == "email" else None
            if channel == "email" and not email:
                continue
            db.add(Delivery(notification_id=note.id, channel=channel, target=email))
            used.append(channel)
        results.append({"user_id": user_id, "status": "created", "notification_id": note.id, "channels": used})
        on_event(db, event=event, user_id=user_id, tenant_id=tenant_id, data=data or {})
    db.commit()
    return results


# ---------------------------------------------------------------- delivery

def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=get_settings().RETRY_BASE_SECONDS * (2 ** max(attempts - 1, 0)))


def deliver(db: Session, providers: Providers, now: datetime | None = None) -> int:
    """Sends the deliveries that are due. Returns how many were handled (sent, retried, failed or skipped)."""
    now = now or datetime.utcnow()
    settings = get_settings()
    due = (db.query(Delivery).filter(Delivery.status == "pending", Delivery.next_attempt_at <= now)
           .order_by(Delivery.next_attempt_at).limit(settings.BATCH_SIZE).all())
    for delivery in due:
        note = db.get(Notification, delivery.notification_id)
        try:
            if delivery.channel == "push":
                devices = db.query(Device).filter(Device.user_id == note.user_id).all()
                if not devices:
                    delivery.status, delivery.last_error = "skipped", "no registered device"
                else:
                    payload = {**note.data, "notification_id": note.id, "event": note.event, "category": note.category}
                    dead = providers.push([d.token for d in devices], note.title, note.body, payload)
                    for device in devices:
                        if device.token in dead:
                            db.delete(device)
                    delivery.status, delivery.sent_at = "sent", now
            elif delivery.channel == "email":
                providers.email(delivery.target, note.title, note.body)
                delivery.status, delivery.sent_at = "sent", now
            else:
                delivery.status, delivery.last_error = "skipped", "unknown channel"
        except Exception as exc:  # a provider failing must never stop the others
            delivery.attempts += 1
            delivery.last_error = f"{type(exc).__name__}: {exc}"[:500]
            if delivery.attempts >= settings.MAX_ATTEMPTS:
                delivery.status = "failed"
            else:
                delivery.next_attempt_at = now + _backoff(delivery.attempts)
            logger.warning("delivery %s failed (attempt %d): %s", delivery.id, delivery.attempts, delivery.last_error)
        db.commit()
    return len(due)


# ---------------------------------------------------------------- nudges

def on_event(db: Session, *, event: str, user_id: str, tenant_id: str | None, data: dict) -> None:
    """Starts the nudges this event triggers and cancels the ones it answers."""
    now = datetime.utcnow()
    for rule in db.query(NudgeRule).filter(NudgeRule.active.is_(True), NudgeRule.cancel_event == event).all():
        subject = str(data.get(rule.subject_field) or "")
        if subject:
            for job in db.query(NudgeJob).filter_by(rule_id=rule.id, user_id=user_id, subject=subject, status="pending").all():
                job.status = "cancelled"
    for rule in db.query(NudgeRule).filter(NudgeRule.active.is_(True), NudgeRule.trigger_event == event).all():
        subject = str(data.get(rule.subject_field) or "")
        if not subject:
            continue
        waiting = db.query(NudgeJob).filter_by(rule_id=rule.id, user_id=user_id, subject=subject, status="pending").first()
        if waiting is None:
            db.add(NudgeJob(rule_id=rule.id, user_id=user_id, tenant_id=tenant_id, subject=subject, data=data,
                            due_at=now + timedelta(minutes=rule.delay_minutes)))


def run_nudges(db: Session, now: datetime | None = None) -> int:
    """Sends the nudges that are due, through the same pipeline as any event. Returns how many were sent."""
    now = now or datetime.utcnow()
    sent = 0
    for job in db.query(NudgeJob).filter(NudgeJob.status == "pending", NudgeJob.due_at <= now).limit(get_settings().BATCH_SIZE).all():
        rule = db.get(NudgeRule, job.rule_id)
        if rule is None or not rule.active:
            job.status = "cancelled"
            continue
        number = job.sent_count + 1
        try:
            ingest(db, source="nudges", event=rule.nudge_event, recipients=[{"user_id": job.user_id}],
                   data={**job.data, rule.subject_field: job.subject, "nudge_number": number}, tenant_id=job.tenant_id,
                   idempotency_key=f"nudge:{job.id}:{number}")
        except UnknownEvent:
            logger.warning("nudge rule %s sends %s, which is not an event type", rule.name, rule.nudge_event)
            job.status = "cancelled"
            db.commit()
            continue
        job.sent_count = number
        if job.sent_count >= rule.max_nudges:
            job.status = "done"
        else:
            job.due_at = now + timedelta(minutes=rule.repeat_minutes or rule.delay_minutes)
        db.commit()
        sent += 1
    db.commit()
    return sent
