"""Review notifications, on the existing platform feed and the generic Socket.IO ``notification`` event.

    review_submitted -> the Enterprise Admin(s) of the business that owns the item    (a new review was saved)
    review_approved  -> the review's author                                           (a moderator approved it)
    review_rejected  -> the review's author                                           (a moderator rejected it)

Sent after the change is committed, from a background thread, and never able to break the API call that
triggered it. Each delivery is claimed first with a unique key (notification_idempotency), so a retry cannot send
it twice. A decision is only announced when the status really changed, and putting a review back to pending
announces nothing.

`module` is one of: training (courses are trainings), product, service, event.
"""
from __future__ import annotations

import logging
import threading
from uuid import UUID

from app.services import notification_idempotency
from app.services.review_common import parse_rating

logger = logging.getLogger(__name__)

CATEGORY_BY_STATUS = {"approved": "review_approved", "rejected": "review_rejected"}


def _dispatch(fn, *args, **kwargs) -> None:
    def runner():
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.exception("review notification delivery failed")

    threading.Thread(target=runner, name="review-notification", daemon=True).start()


def _routing_metadata(category: str, metadata: dict) -> dict:
    return {k: v for k, v in {**metadata, "category": category}.items() if v is not None}


def _load(db, module: str, review_id: UUID):
    """(review, item, item_name, item_id_key) or None. `item` is the Training/Product/Service/Event."""
    if module == "training":
        from app.models.training_model import Training, TrainingReview

        review = db.get(TrainingReview, review_id)
        item = db.get(Training, review.training_id) if review else None
        return (review, item, getattr(item, "title", None), "training_id") if item else None
    if module == "product":
        from app.models.product_model import Product, ProductReview

        review = db.get(ProductReview, review_id)
        item = db.get(Product, review.product_id) if review else None
        return (review, item, getattr(item, "product_name", None), "product_id") if item else None
    if module == "service":
        from app.models.service_model import Service, ServiceReview

        review = db.get(ServiceReview, review_id)
        item = db.get(Service, review.service_id) if review else None
        return (review, item, getattr(item, "service_name", None), "service_id") if item else None
    if module == "event":
        from app.models.event_aux_models import EventFeedback
        from app.models.event_model import Event

        review = db.get(EventFeedback, review_id)
        item = db.get(Event, review.event_id) if review and review.is_review else None
        return (review, item, getattr(item, "title", None), "event_id") if item else None
    return None


def _author_user_id(db, module: str, review, item, access_token: str | None) -> UUID | None:
    if module in ("product", "service"):
        return review.user_id
    if module == "event":
        return getattr(review, "user_id", None)
    if module == "training":
        from app.models.training_model import TrainingEnrolment
        from app.services.training_learner_identity import resolve_learner_user_id

        enrolment = db.query(TrainingEnrolment).filter(
            TrainingEnrolment.training_id == review.training_id,
            TrainingEnrolment.participant_email == review.participant_email,
        ).first()
        if enrolment is None:
            return None
        return resolve_learner_user_id(db, enrolment, item, access_token=access_token)
    return None


def _deliver(db, *, recipients, category, title, message, tenant_id, metadata, dedupe_key):
    from app.services.notification_service import create_automatic_notification

    if not recipients:
        logger.warning("%s NOT delivered: no recipients (%s)", category, dedupe_key)
        return None
    if not notification_idempotency.claim(db, dedupe_key):
        logger.info("%s skipped: already delivered (%s)", category, dedupe_key)
        return None
    try:
        return create_automatic_notification(
            db, title=title, message=message, category=category, user_ids=list(recipients),
            tenant_id=tenant_id, metadata=_routing_metadata(category, metadata), channels=["in_app", "push"],
        )
    except Exception:
        logger.exception("%s delivery failed; releasing claim %s", category, dedupe_key)
        db.rollback()
        notification_idempotency.release(db, dedupe_key)
        return None


# --- a new review -> the business's Enterprise Admin(s) --------------------------------------------------

def notify_review_submitted(module: str, review_id, *, access_token: str | None = None) -> None:
    """Call after a NEW review has been committed (an edit of an existing review is not announced)."""
    _dispatch(_deliver_submitted, module, str(review_id), access_token)


def _deliver_submitted(module: str, review_id: str, access_token: str | None):
    from app.db.database import SessionLocal
    from app.services import event_notification_service as recipients_of

    with SessionLocal() as db:
        loaded = _load(db, module, UUID(review_id))
        if loaded is None:
            return None
        review, item, name, id_key = loaded
        recipients, tenant_id = recipients_of.resolve_enterprise_admin_user_ids(db, item, access_token=access_token)
        rating = parse_rating(review.rating)
        stars = f"{rating} star" + ("" if rating == 1 else "s") if rating else "A"
        return _deliver(
            db, recipients=recipients, category="review_submitted",
            title="New review", message=f'{stars} review on "{name or "your item"}" is waiting for approval.',
            tenant_id=tenant_id,
            metadata={
                id_key: str(item.id), "entity_type": module, "entity_id": str(item.id),
                "review_id": review_id, "rating": rating, "status": review.moderation_status,
            },
            dedupe_key=f"review_submitted:{module}:{review_id}",
        )


# --- a decision -> the author ---------------------------------------------------------------------------

def notify_review_decision(module: str, review_id, new_status: str, *, access_token: str | None = None) -> None:
    """Call after a moderator changed the status from something else to `new_status` and it was committed.
    Only approved / rejected are announced."""
    if new_status not in CATEGORY_BY_STATUS:
        return
    _dispatch(_deliver_decision, module, str(review_id), new_status, access_token)


def _deliver_decision(module: str, review_id: str, new_status: str, access_token: str | None):
    from app.db.database import SessionLocal

    category = CATEGORY_BY_STATUS[new_status]
    with SessionLocal() as db:
        loaded = _load(db, module, UUID(review_id))
        if loaded is None:
            return None
        review, item, name, id_key = loaded
        if review.moderation_status != new_status:
            return None  # changed again since; the newer change announces itself
        author = _author_user_id(db, module, review, item, access_token)
        if author is None:
            logger.info("%s not sent: review %s has no resolvable author", category, review_id)
            return None
        shown = f'"{name}"' if name else "your item"
        if new_status == "approved":
            title, message = "Review approved", f"Your review of {shown} was approved and is now visible."
        else:
            title, message = "Review not approved", f"Your review of {shown} was not approved."
        stamp = getattr(review, "updated_at", None) or review.created_at
        version = stamp.isoformat() if stamp else ""
        return _deliver(
            db, recipients=[author], category=category, title=title, message=message, tenant_id=None,
            metadata={
                id_key: str(item.id), "entity_type": module, "entity_id": str(item.id),
                "review_id": review_id, "status": new_status,
            },
            dedupe_key=f"{category}:{module}:{review_id}:{version}",
        )
