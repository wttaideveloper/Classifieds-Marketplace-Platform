"""Training-module notifications: in-app/push (platform inbox) plus the two email triggers.

Every public ``notify_*`` function extracts plain values from the ORM objects it is given and
hands the actual delivery to ``_dispatch`` (a daemon thread), so a slow SMTP server or a large
fan-out never blocks the API request that caused it. Delivery always uses its own database
session, so a failure here can never poison the caller's transaction.

Email goes out only for the triggers that ask for it (enrolment approved, certificate earned,
and announcements whose channel is email/both). Email is sent directly, once — the in-app
pipeline is called with ["in_app", "push"] only, so nobody is emailed twice.
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core.config import settings

logger = logging.getLogger(__name__)

# Learners who are actually in the training — pending/rejected/cancelled/waitlisted people are not.
AUDIENCE_STATUSES = ("enrolled", "attended")

# Local (training time zone) hour from which the reminder for that calendar day may go out.
REMINDER_HOUR_LOCAL = 9

KIND_DAY_BEFORE = "reminder_day_before"
KIND_FINAL_DAY = "final_day"


def _dispatch(fn, *args, **kwargs) -> None:
    def runner():
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.exception("training notification delivery failed")

    threading.Thread(target=runner, name="training-notification", daemon=True).start()


def _as_uuid(value) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def _routing_metadata(category: str, metadata: dict | None) -> dict:
    """What mobile reads to open the right screen. The push `data` block is built from this
    dict alone (the FCM message has no separate category field), so `category` must live here,
    and None values are dropped because FCM stringifies them to the literal text "None"."""
    return {k: v for k, v in {**(metadata or {}), "category": category}.items() if v is not None}


def deliver_to_participant(
    *,
    tenant_id,
    email: str | None,
    user_id,
    title: str,
    message: str,
    category: str,
    metadata: dict | None = None,
    send_email: bool = False,
    in_app: bool = True,
) -> dict:
    """Synchronous delivery to one person. Returns {"in_app": bool, "email": bool}; never raises."""
    result = {"in_app": False, "email": False}
    metadata = _routing_metadata(category, metadata)

    if send_email and email:
        try:
            from app.services.notification_triggers import _send_email_via_smtp

            result["email"] = _send_email_via_smtp(email, title, message)
        except Exception:
            logger.exception("training email to %s failed", email)

    if not in_app:
        return result

    try:
        from app.db.database import SessionLocal
        from app.services.notification_service import create_automatic_notification

        with SessionLocal() as db:
            uid = _as_uuid(user_id)
            if uid is None and email:
                from app.services.notification_triggers import _resolve_user_id_from_email

                uid = _resolve_user_id_from_email(db, email)
            if uid is None:
                logger.info("training notification %s: no user id for %s — in-app skipped", category, email)
                return result
            create_automatic_notification(
                db, title=title, message=message, category=category, user_ids=[uid],
                tenant_id=_as_uuid(tenant_id), metadata=metadata, channels=["in_app", "push"],
            )
            result["in_app"] = True
    except Exception:
        logger.exception("training in-app notification %s failed", category)
    return result


def _person(enrolment) -> dict:
    return {
        "email": enrolment.participant_email,
        "name": enrolment.participant_name,
        "user_id": getattr(enrolment, "user_id", None),
    }


def _send(training, person: dict, *, title, message, category, metadata, send_email=False, in_app=True) -> None:
    _dispatch(
        deliver_to_participant,
        tenant_id=training.tenant_id, email=person["email"], user_id=person.get("user_id"),
        title=title, message=message, category=category,
        metadata={"training_id": str(training.id), **metadata},
        send_email=send_email, in_app=in_app,
    )


# --- 1. enrolment lifecycle (approved also emails) ---------------------------------------

def notify_enrolment_pending(training, enrolment) -> None:
    _send(
        training, _person(enrolment),
        title=f"Enrolment Pending: {training.title}",
        message=f"Hi {enrolment.participant_name}, your enrolment in {training.title} is awaiting admin approval.",
        category="training_enrolment_confirmation",
        metadata={"status": "pending_approval", "enrolment_id": str(enrolment.id)},
    )


def notify_enrolment_confirmed(training, enrolment) -> None:
    _send(
        training, _person(enrolment),
        title=f"Enrolment Confirmed: {training.title}",
        message=f"Hi {enrolment.participant_name}, your enrolment in {training.title} is confirmed.",
        category="training_enrolment_confirmation",
        metadata={"status": "enrolled", "enrolment_id": str(enrolment.id)},
    )


def notify_enrolment_approved(training, enrolment) -> None:
    _send(
        training, _person(enrolment),
        title=f"Enrolment Approved: {training.title}",
        message=f"Hi {enrolment.participant_name}, your enrolment in {training.title} has been approved. "
                f"You can start learning from the training page.",
        category="enrolment_approved",
        metadata={"status": "enrolled", "enrolment_id": str(enrolment.id)},
        send_email=True,
    )


def notify_enrolment_rejected(training, enrolment, reason: str | None) -> None:
    message = f"Hi {enrolment.participant_name}, your enrolment in {training.title} was not approved."
    if reason:
        message += f" Reason: {reason}"
    _send(
        training, _person(enrolment),
        title=f"Enrolment Rejected: {training.title}", message=message,
        category="enrolment_rejected",
        metadata={"status": "rejected", "enrolment_id": str(enrolment.id), "reason": reason},
    )


def notify_enrolment_cancelled(training, enrolment) -> None:
    _send(
        training, _person(enrolment),
        title=f"Enrolment Cancelled: {training.title}",
        message=f"Hi {enrolment.participant_name}, your enrolment in {training.title} has been cancelled.",
        category="enrolment_cancelled",
        metadata={"status": "cancelled", "enrolment_id": str(enrolment.id)},
    )


# --- 3. completion / certificate (also emails) -------------------------------------------

def notify_certificate_ready(training, *, email: str, name: str | None, user_id, certificate_url: str | None) -> None:
    display = name or email
    _send(
        training, {"email": email, "name": display, "user_id": user_id},
        title=f"Certificate Earned: {training.title}",
        message=f"Congratulations {display}! You have completed {training.title}. "
                f"Your certificate is ready — open the training to download it.",
        category="training_certificate",
        metadata={"certificate_url": certificate_url},
        send_email=True,
    )


# --- 5. admin answers a question ----------------------------------------------------------

def notify_question_answered(training, *, discussion: dict, answer_text: str, answered_by: str | None) -> None:
    author_email = discussion.get("author")
    if not author_email or "@" not in str(author_email) or author_email == answered_by:
        return
    preview = (answer_text or "").strip()
    if len(preview) > 200:
        preview = preview[:197] + "..."
    _send(
        training, {"email": author_email, "name": author_email, "user_id": discussion.get("author_id")},
        title=f"Your question was answered: {training.title}",
        message=f"Your question in {training.title} has been answered: {preview}",
        category="training_answer",
        metadata={"discussion_id": discussion.get("id")},
    )


# --- 4. announcements ---------------------------------------------------------------------

def audience_for_training(db, training_id) -> list[dict]:
    from app.models.training_model import TrainingEnrolment

    rows = db.query(TrainingEnrolment).filter(
        TrainingEnrolment.training_id == training_id,
        TrainingEnrolment.status.in_(AUDIENCE_STATUSES),
    ).all()
    seen, people = set(), []
    for r in rows:
        key = (r.participant_email or "").lower()
        if key and key not in seen:
            seen.add(key)
            people.append(_person(r))
    return people


def notify_announcement(db, training, entry: dict) -> int:
    """Fan an announcement out to everyone enrolled. Honors the announcement's channel:
    in_app (default) / both -> in-app; email / both -> email; email-only skips the inbox."""
    channel = (entry.get("channel") or "in_app").lower()
    send_email = channel in ("email", "both")
    in_app = channel != "email"
    title = entry.get("title") or f"Announcement: {training.title}"
    people = audience_for_training(db, training.id)
    for person in people:
        _send(
            training, person, title=title, message=entry.get("message") or "",
            category="training_announcement",
            metadata={"announcement_id": entry.get("id")}, send_email=send_email, in_app=in_app,
        )
    return len(people)


# --- 2. new training published ------------------------------------------------------------

def _fan_out_new_training(*, training_id, tenant_id, title, message, metadata, exclude_user_id) -> int:
    from app.db.database import SessionLocal
    from app.services.invigorate_auth_client import list_tenant_user_ids
    from app.services.notification_service import create_automatic_notification

    tenant_uuid = _as_uuid(tenant_id)
    if tenant_uuid is None:
        logger.info("new-training notification skipped for %s: training has no tenant", training_id)
        return 0
    exclude = _as_uuid(exclude_user_id)
    user_ids = [uid for uid in list_tenant_user_ids(tenant_uuid) if uid != exclude]
    if not user_ids:
        logger.info("new-training notification for %s: no tenant users resolved (Invigorate internal API configured?)", training_id)
        return 0
    with SessionLocal() as db:
        create_automatic_notification(
            db, title=title, message=message, category="training_new", user_ids=user_ids,
            tenant_id=tenant_uuid, metadata=_routing_metadata("training_new", metadata), channels=["in_app", "push"],
        )
    return len(user_ids)


def notify_new_training(training, *, exclude_user_id=None) -> None:
    when = f" Starts {training.start_date:%d %b %Y}." if getattr(training, "start_date", None) else ""
    _dispatch(
        _fan_out_new_training,
        training_id=str(training.id), tenant_id=training.tenant_id,
        title=f"New Training: {training.title}",
        message=f"{training.title} has just been added.{when} Take a look and enrol.",
        metadata={"training_id": str(training.id)}, exclude_user_id=exclude_user_id,
    )


# --- 6 & 7. scheduled reminders -----------------------------------------------------------

def _training_tz(training):
    from app.utils.event_utils import get_event_timezone

    return get_event_timezone(training)


def _claim(db, training_id, email: str, kind: str, ref_date: str) -> bool:
    from app.models.training_model import TrainingNotificationLog

    db.add(TrainingNotificationLog(training_id=training_id, participant_email=email, kind=kind, ref_date=ref_date))
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def _reminder_kinds_due(training, local_now: datetime) -> list[tuple[str, str]]:
    """[(kind, ref_date)] due today for this training, judged in the training's own time zone."""
    due = []
    today = local_now.date()
    if training.start_date and today == training.start_date.date() - timedelta(days=1):
        due.append((KIND_DAY_BEFORE, training.start_date.date().isoformat()))
    last_day = training.end_date or training.start_date
    if last_day and today == last_day.date():
        due.append((KIND_FINAL_DAY, last_day.date().isoformat()))
    return due


def _reminder_text(training, person: dict, kind: str) -> tuple[str, str]:
    name = person.get("name") or person["email"]
    if kind == KIND_DAY_BEFORE:
        when = f"{training.start_date:%d %b %Y}"
        if training.start_time:
            when += f" at {training.start_time}"
        where = f" Venue: {training.venue}." if training.delivery_mode in ("physical", "hybrid") and training.venue else ""
        return (
            f"Reminder: {training.title} starts tomorrow",
            f"Hi {name}, {training.title} starts tomorrow ({when}).{where}",
        )
    return (
        f"Final day: {training.title}",
        f"Hi {name}, today is the final day of {training.title}. "
        f"Make sure you have finished your lessons and assessments.",
    )


def send_due_training_reminders(db, now_utc: datetime | None = None) -> dict:
    """One scheduler tick. Safe to run concurrently in many workers: each (training, learner,
    kind, date) is claimed in training_notification_log before sending, so it goes out once."""
    from sqlalchemy import or_

    from app.models.training_model import Training

    now_utc = now_utc or datetime.now(timezone.utc)
    naive_now = now_utc.replace(tzinfo=None)
    lo, hi = naive_now - timedelta(days=2), naive_now + timedelta(days=3)
    trainings = db.query(Training).filter(
        Training.status == "published",
        Training.is_deleted.is_(False),
        or_(Training.start_date.between(lo, hi), Training.end_date.between(lo, hi)),
    ).all()

    sent = {KIND_DAY_BEFORE: 0, KIND_FINAL_DAY: 0}
    for training in trainings:
        local_now = now_utc.astimezone(_training_tz(training))
        if local_now.hour < REMINDER_HOUR_LOCAL:
            continue
        due = _reminder_kinds_due(training, local_now)
        if not due:
            continue
        people = audience_for_training(db, training.id)
        for kind, ref_date in due:
            for person in people:
                if not _claim(db, training.id, person["email"], kind, ref_date):
                    continue
                title, message = _reminder_text(training, person, kind)
                deliver_to_participant(
                    tenant_id=training.tenant_id, email=person["email"], user_id=person.get("user_id"),
                    title=title, message=message,
                    category="training_reminder" if kind == KIND_DAY_BEFORE else "training_final_day",
                    metadata={"training_id": str(training.id), "kind": kind, "date": ref_date},
                )
                sent[kind] += 1
    return sent


def _scheduler_loop(interval_seconds: int) -> None:
    from app.db.database import SessionLocal

    while True:
        try:
            with SessionLocal() as db:
                result = send_due_training_reminders(db)
            if any(result.values()):
                logger.info("training reminders sent: %s", result)
        except Exception:
            logger.exception("training reminder tick failed")
        time.sleep(interval_seconds)


def start_reminder_scheduler() -> threading.Thread | None:
    """Started from app startup. No Celery worker/beat exists in this deployment, so reminders
    run on a daemon thread; the claim log makes running it in every worker harmless."""
    if not settings.training_reminders_enabled:
        return None
    thread = threading.Thread(
        target=_scheduler_loop, args=(settings.TRAINING_REMINDER_INTERVAL_SECONDS,),
        name="training-reminders", daemon=True,
    )
    thread.start()
    logger.info("Training reminder scheduler started (every %ss)", settings.TRAINING_REMINDER_INTERVAL_SECONDS)
    return thread
