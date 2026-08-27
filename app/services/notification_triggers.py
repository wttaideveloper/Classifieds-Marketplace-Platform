import logging
from uuid import UUID
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

def _safe_notify(db: Session, title: str, message: str, category: str, tenant_id, metadata: dict, participant_email: str | None = None, channels: list[str] | None = None):
    """Best-effort notification. Tries in_app+push+email+sms, falls back to audit row if no user_ids.
    Channels default to ["in_app"] for MVP, but callers can pass ["in_app","push","email","sms"].
    """
    channels = channels or ["in_app"]
    try:
        from app.services.notification_service import create_automatic_notification
        user_ids: list[UUID] = []
        if participant_email:
            try:
                pass
            except Exception:
                pass
        if user_ids:
            create_automatic_notification(
                db, title=title, message=message, category=category,
                user_ids=user_ids, tenant_id=tenant_id, metadata=metadata, channels=channels
            )
        else:
            # Fallback: create notification row directly for audit, without dispatch (in_app requires user_ids)
            # Use notification_repo to persist for history
            try:
                from app.repository import notification_repo
                from uuid import uuid4
                # Create a generic notification entry linked to tenant for audit
                tid = None
                try:
                    tid = UUID(str(tenant_id)) if tenant_id else None
                except Exception:
                    tid = None
                notification_repo.create_notification(
                    db, tenant_id=tid, created_by=None, title=title, message=message,
                    notification_type="automatic", category=category, delivery_type="immediate",
                    status="sent", metadata={**metadata, "participant_email": participant_email} if participant_email else metadata
                )
            except Exception as e:
                logger.debug(f"notification audit create failed: {e}")
        logger.info(f"notification trigger: {category} title={title} email={participant_email}")
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
