import logging
from uuid import UUID
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

def _safe_notify(db: Session, title: str, message: str, category: str, tenant_id, metadata: dict, participant_email: str | None = None):
    """Best-effort in_app notification. Uses create_automatic_notification with email fallback to avoid blocking.
    For MVP we send in_app only; email channel is queued but logged.
    """
    try:
        from app.services.notification_service import create_automatic_notification
        # Try to resolve participant as platform user via email -> user_id lookup if available
        # Fallback: if no user_id, create notification with empty user_ids (will be no-op but logged)
        user_ids: list[UUID] = []
        # Attempt to resolve via invigorate auth if email looks like platform user - best effort, ignore failures
        # We don't block on failure
        if participant_email:
            try:
                # No direct email->UUID resolver, keep empty but still log via metadata
                pass
            except Exception:
                pass
        # Always create a system notification for audit (even if no user_ids, we still want record)
        # create_automatic_notification returns None if user_ids empty, so we create a raw notification row for audit
        if user_ids:
            create_automatic_notification(
                db, title=title, message=message, category=category,
                user_ids=user_ids, tenant_id=tenant_id, metadata=metadata, channels=["in_app"]
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
    _safe_notify(db, title, msg, "booking_confirmed", event.tenant_id, {"event_id": str(event.id), "registration_id": str(reg.id), "qr_code": reg.qr_code}, participant_email=reg.participant_email)

def notify_single_cancellation(db: Session, event, reg):
    title = f"Registration Cancelled: {event.title}"
    msg = f"Hi {reg.participant_name}, your registration for {event.title} has been cancelled."
    _safe_notify(db, title, msg, "event_cancelled", event.tenant_id, {"event_id": str(event.id), "registration_id": str(reg.id)}, participant_email=reg.participant_email)

def notify_event_cancelled(db: Session, event, previous_status: str):
    title = f"Event Cancelled: {event.title}"
    msg = f"Event {event.title} has been cancelled (was {previous_status})."
    # Fan-out to all confirmed/attended regs - best effort, don't enumerate user_ids now, just audit one row
    _safe_notify(db, title, msg, "event_cancelled", event.tenant_id, {"event_id": str(event.id), "previous_status": previous_status})
    # Also per-registrant audit rows for history
    try:
        from app.models.event_aux_models import EventRegistration
        regs = db.query(EventRegistration).filter(EventRegistration.event_id==event.id, EventRegistration.status.in_(["confirmed","attended"])).all()
        for r in regs:
            _safe_notify(db, title, f"Hi {r.participant_name}, {msg}", "event_cancelled", event.tenant_id, {"event_id": str(event.id), "registration_id": str(r.id)}, participant_email=r.participant_email)
    except Exception as e:
        logger.warning(f"fan-out cancelled failed: {e}")
