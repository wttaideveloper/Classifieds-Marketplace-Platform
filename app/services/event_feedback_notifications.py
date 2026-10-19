"""A feedback form was sent for an event -> the event's Enterprise Admin(s) and the Platform Super Admin(s).

    event_feedback_submitted   in-app and push; the attendee's text is in metadata.comment (feed and realtime event only,
                               never in the push payload)

Sent after the feedback is saved, from a background thread, and never able to break the API call. Each copy is claimed with a
unique key first, so a retry cannot send it twice. Reviews (stars with a comment) are announced by review_submitted instead.
"""
from __future__ import annotations

import logging
from uuid import UUID

from app.services import review_notifications as rn
from app.services.review_common import parse_rating

logger = logging.getLogger(__name__)

CATEGORY = "event_feedback_submitted"


def notify_feedback_submitted(feedback_id, *, access_token: str | None = None) -> None:
    """Call after a feedback form has been committed."""
    try:
        rn._dispatch(_deliver_feedback_submitted, str(feedback_id), access_token)
    except Exception:
        logger.exception("could not queue the %s notification", CATEGORY)


def _deliver_feedback_submitted(feedback_id: str, access_token: str | None):
    from app.db.database import SessionLocal
    from app.models.event_aux_models import EventFeedback
    from app.models.event_model import Event
    from app.services import event_notification_service as recipients_of
    from app.services.event_service import _reviewer_names

    with SessionLocal() as db:
        feedback = db.get(EventFeedback, UUID(feedback_id))
        if feedback is None or feedback.is_review:
            return None
        event = db.get(Event, feedback.event_id)
        if event is None:
            return None

        owners, tenant_id = recipients_of.resolve_enterprise_admin_user_ids(db, event, access_token=access_token)
        platform = [u for u in recipients_of.resolve_platform_admin_user_ids(access_token) if u not in set(owners)]
        email = (feedback.participant_email or "").strip().lower()
        attendee = rn._clean_name(_reviewer_names(db, event.id, [feedback]).get(email))
        title_text = getattr(event, "title", None) or "your event"
        rating = parse_rating(feedback.rating)
        metadata = {
            "event_id": str(event.id), "item_id": str(event.id), "entity_type": "event", "entity_id": str(event.id),
            "event_title": getattr(event, "title", None), "item_name": getattr(event, "title", None),
            "feedback_id": str(feedback.id), "attendee_name": attendee, "rating": rating,
            "form_id": feedback.form_id, "comment": (feedback.comment or "").strip() or None,
            "submitted_at": feedback.created_at.isoformat() if feedback.created_at else None,
        }
        title = "New event feedback"
        message = f'{attendee or "An attendee"} sent feedback on "{title_text}".'
        delivered = rn._deliver(
            db, recipients=owners, category=CATEGORY, title=title, message=message, tenant_id=tenant_id, metadata=metadata,
            dedupe_key=f"{CATEGORY}:{feedback_id}",
        )
        rn._deliver(
            db, recipients=platform, category=CATEGORY, title=title, message=message, tenant_id=None, metadata=metadata,
            dedupe_key=f"{CATEGORY}:{feedback_id}:platform",
        )
        return delivered
