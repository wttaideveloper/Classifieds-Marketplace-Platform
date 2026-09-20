"""Celery tasks for event background processing."""

from uuid import UUID
from datetime import datetime, timezone

from app.celery_app import celery_app


@celery_app.task(name="events.expire_waitlist_offer")
def expire_waitlist_offer_task(waitlist_id: str):
    from app.db.database import SessionLocal
    from app.models.event_aux_models import EventWaitlist
    from app.services.event_service import _try_promote_from_waitlist
    import logging

    logger = logging.getLogger(__name__)

    db = SessionLocal()
    try:
        # We need an exclusive lock to ensure no race condition with a concurrent checkout
        waitlist_entry = db.query(EventWaitlist).filter(
            EventWaitlist.id == UUID(waitlist_id)
        ).with_for_update().first()

        if not waitlist_entry:
            return {"status": "not_found"}

        if waitlist_entry.status != "payment_pending":
            return {"status": "already_processed"}

        if waitlist_entry.payment_offer_expires_at and waitlist_entry.payment_offer_expires_at > datetime.utcnow():
            return {"status": "not_yet_expired"}

        logger.info(f"Waitlist offer {waitlist_id} expired. Releasing seat for event {waitlist_entry.event_id}.")
        
        # Mark expired
        waitlist_entry.status = "expired"
        event_id = waitlist_entry.event_id
        
        db.commit()

        # The seat is now released. We should try to promote the next person in line.
        try:
            # Re-run promotion logic to pick the next user
            _try_promote_from_waitlist(db, event_id)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to promote next user after waitlist expiration: {e}")

        return {"status": "expired"}
    except Exception as e:
        db.rollback()
        logger.error(f"Error expiring waitlist offer {waitlist_id}: {e}")
        raise
    finally:
        db.close()
