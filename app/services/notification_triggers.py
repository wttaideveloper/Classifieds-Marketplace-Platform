import logging
import smtplib
from uuid import UUID
from email.message import EmailMessage
from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger(__name__)


def _send_email_via_smtp(recipient_email: str, title: str, body: str) -> bool:
    """Send a plain-text email using the configured SMTP settings.

    Returns True if the message was accepted by the SMTP server, False otherwise.
    """
    try:
        msg = EmailMessage()
        msg["From"] = settings.email_user
        msg["To"] = recipient_email
        msg["Subject"] = title
        msg.set_content(body)

        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.starttls()
            smtp.login(settings.email_user, settings.email_pass)
            smtp.send_message(msg)
        return True
    except Exception as e:
        logger.warning(f"SMTP email send failed to {recipient_email}: {e}")
        return False


def _resolve_user_id_from_email(db: Session, participant_email: str) -> UUID | None:
    """Best-effort lookup: try to find a user ID by email across relevant tables.

    Returns a UUID if found, None otherwise. Does not raise.
    """
    try:
        from app.models import User, Organizer, Provider
        # Try User table first
        user = db.query(User).filter_by(email=participant_email).first()
        if user:
            return UUID(str(user.id))
        # Try organizer table
        org = db.query(Organizer).filter_by(email=participant_email).first()
        if org:
            return UUID(str(org.id))
        # Try provider table
        prov = db.query(Provider).filter_by(email=participant_email).first()
        if prov:
            return UUID(str(prov.id))
    except Exception as e:
        logger.debug(f"User lookup by email {participant_email} failed: {e}")
    return None


def _emit_inapp_notification(db: Session, user_id: UUID, title: str, message: str, metadata: dict):
    """Emit an in-app notification via the realtime emitter if available."""
    try:
        from app.realtime.emitters import emit_notification
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(emit_notification(str(user_id), {"notification_id": str(uuid4()), "title": title, "message": message, "metadata": metadata}))
        else:
            loop.run_until_complete(emit_notification(str(user_id), {"notification_id": str(uuid4()), "title": title, "message": message, "metadata": metadata}))
    except Exception as e:
        logger.debug(f"In-app emit failed for user_id={user_id}: {e}")


def _safe_notify(db: Session, title: str, message: str, category: str, tenant_id, metadata: dict, participant_email: str | None = None, channels: list[str] | None = None):
    """Best-effort notification.

    - If participant_email is available, send email directly via SMTP (best-effort, does not depend on user‑ID resolution).
    - If a user_id can be resolved from the email, also emit in‑app/push via the normal pipeline.
    - Falls back to an audit row only if nothing else succeeds.
    Channels default to ["in_app"] for MVP, but callers can pass ["in_app","push","email","sms"].
    """
    channels = channels or ["in_app"]
    try:
        from app.services.notification_service import create_automatic_notification
        import uuid

        user_ids: list[UUID] = []

        # 1) Try to resolve a user_id from participant_email for in‑app/push pipeline
        if participant_email:
            resolved_id = _resolve_user_id_from_email(db, participant_email)
            if resolved_id:
                user_ids.append(resolved_id)
                _emit_inapp_notification(db, resolved_id, title, message, metadata)

        # 2) Send email directly if participant_email is available — independent of user_id resolution
        if participant_email and "email" in channels:
            sent = _send_email_via_smtp(participant_email, title, message)
            if sent:
                logger.info(f"Email sent via SMTP to {participant_email} for {category}")

        # 3) If we have at least one user_id, dispatch the automatic notification (in-app/push)
        if user_ids:
            create_automatic_notification(
                db, title=title, message=message, category=category,
                user_ids=user_ids, tenant_id=tenant_id, metadata=metadata, channels=channels
            )
        else:
            # No user_id resolved — still record an audit row so we have a trace,
            # but do NOT claim email was sent (email was handled directly above)
            try:
                from app.repository import notification_repo
                tid = None
                try:
                    tid = UUID(str(tenant_id)) if tenant_id else None
                except Exception:
                    tid = None
                notification_repo.create_notification(
                    db, tenant_id=tid, created_by=None, title=title, message=message,
                    notification_type="automatic", category=category, delivery_type="immediate",
                    status="audit", metadata={**metadata, "participant_email": participant_email} if participant_email else metadata,
                    error_message="No user_id resolved; email sent separately if participant_email available."
                )
            except Exception as e:
                logger.debug(f"audit row create failed: {e}")

        logger.info(f"notification trigger: {category} title={title} email={participant_email} user_ids={user_ids}")
    except Exception as e:
        logger.warning(f"notification trigger failed {category}: {e}")

def notify_registration_confirmation(db: Session, event, reg):
    title = f"Registration Confirmed: {event.title}"
    msg = f"Hi {reg.participant_name}, your registration for {event.title} is confirmed. QR: {reg.qr_code}"
    _safe_notify(db, title, msg, "booking_confirmed", event.tenant_id, {"event_id": str(event.id), "registration_id": str(reg.id), "qr_code": reg.qr_code}, participant_email=reg.participant_email, channels=["in_app","push","email","sms"])

def notify_single_cancellation(db: Session, event, reg):
    title = f"Registration Cancelled: {event.title}"
    msg = f"Hi {reg.participant_name}, your registration for {event.title} has been cancelled."
    _safe_notify(db, title, msg, "event_cancelled", event.tenant_id, {"event_id": str(event.id), "registration_id": str(reg.id)}, participant_email=reg.participant_email, channels=["in_app","push","email","sms"])

def notify_event_cancelled(db: Session, event, previous_status: str):
    title = f"Event Cancelled: {event.title}"
    msg = f"Event {event.title} has been cancelled (was {previous_status})."
    _safe_notify(db, title, msg, "event_cancelled", event.tenant_id, {"event_id": str(event.id), "previous_status": previous_status}, channels=["in_app","push","email","sms"])
    try:
        from app.models.event_aux_models import EventRegistration
        regs = db.query(EventRegistration).filter(EventRegistration.event_id==event.id, EventRegistration.status.in_(["confirmed","attended"])).all()
        for r in regs:
            _safe_notify(db, title, f"Hi {r.participant_name}, {msg}", "event_cancelled", event.tenant_id, {"event_id": str(event.id), "registration_id": str(r.id)}, participant_email=r.participant_email, channels=["in_app","push","email","sms"])
    except Exception as e:
        logger.warning(f"fan-out cancelled failed: {e}")

def notify_payment_success(db: Session, event, order):
    title = f"Payment Confirmed: {event.title}"
    msg = f"Hi {order.participant_name}, payment of {order.amount} {order.currency} for {event.title} confirmed. Order {order.id}"
    _safe_notify(db, title, msg, "payment_success", event.tenant_id, {"event_id": str(event.id), "order_id": str(order.id), "amount": str(order.amount)}, participant_email=order.participant_email, channels=["in_app","push","email","sms"])

def notify_payment_failed(db: Session, event, order, reason: str | None = None):
    title = f"Payment Failed: {event.title}"
    msg = f"Hi {order.participant_name}, payment for {event.title} failed" + (f": {reason}" if reason else ".")
    _safe_notify(db, title, msg, "payment_failed", event.tenant_id, {"event_id": str(event.id), "order_id": str(order.id)}, participant_email=order.participant_email, channels=["in_app","push","email","sms"])

def notify_event_reminder(db: Session, event):
    title = f"Reminder: {event.title} tomorrow"
    msg = f"Reminder: {event.title} starts {event.start_date}. Venue: {event.venue} Meeting: {event.meeting_link}"
    try:
        from app.models.event_aux_models import EventRegistration
        regs = db.query(EventRegistration).filter(EventRegistration.event_id==event.id, EventRegistration.status.in_(["confirmed","attended"])).all()
        for r in regs:
            _safe_notify(db, title, f"Hi {r.participant_name}, {msg}", "event_reminder", event.tenant_id, {"event_id": str(event.id), "registration_id": str(r.id)}, participant_email=r.participant_email, channels=["in_app","push","email","sms"])
    except Exception as e:
        logger.warning(f"reminder failed: {e}")

def notify_schedule_change(db: Session, event, changes: dict):
    title = f"Update: {event.title} schedule changed"
    msg = f"Event {event.title} updated: " + ", ".join([f"{k}: {v}" for k,v in changes.items()])
    try:
        from app.models.event_aux_models import EventRegistration
        regs = db.query(EventRegistration).filter(EventRegistration.event_id==event.id, EventRegistration.status.in_(["confirmed","attended"])).all()
        for r in regs:
            _safe_notify(db, title, f"Hi {r.participant_name}, {msg}", "event_schedule_change", event.tenant_id, {"event_id": str(event.id), "changes": changes, "registration_id": str(r.id)}, participant_email=r.participant_email, channels=["in_app","push","email","sms"])
    except Exception as e:
        logger.warning(f"schedule change notify failed: {e}")

def notify_refund_status(db: Session, event, order_or_reg, status: str):
    title = f"Refund {status}: {event.title}"
    email = getattr(order_or_reg, "participant_email", None)
    msg = f"Refund status for {event.title}: {status}"
    _safe_notify(db, title, msg, "refund_status", event.tenant_id, {"event_id": str(event.id), "status": status, "id": str(getattr(order_or_reg, 'id', ''))}, participant_email=email, channels=["in_app","push","email","sms"])
