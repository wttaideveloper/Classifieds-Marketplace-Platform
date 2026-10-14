"""Review notifications, on the existing platform feed and the generic Socket.IO ``notification`` event.

    review_submitted -> the Enterprise Admin(s) of the business that owns the item, and the Super Admin(s)
                        (a new review was posted, already approved; or a rejected one was edited and resubmitted,
                        which is pending and waits for a Super Admin)
    review_approved  -> the review's author                                           (a Super Admin approved it)
    review_rejected  -> the review's author                                           (a Super Admin rejected it)

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


def _load_item(db, module: str, item_id):
    """The Training / Product / Service / Event a review belongs to (the review itself may already be deleted)."""
    try:
        key = UUID(str(item_id))
    except (ValueError, TypeError):
        return None
    if module == "training":
        from app.models.training_model import Training

        return db.get(Training, key)
    if module == "product":
        from app.models.product_model import Product

        return db.get(Product, key)
    if module == "service":
        from app.models.service_model import Service

        return db.get(Service, key)
    if module == "event":
        from app.models.event_model import Event

        return db.get(Event, key)
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


def _deliver(db, *, recipients, category, title, message, tenant_id, metadata, dedupe_key, channels=None):
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
            tenant_id=tenant_id, metadata=_routing_metadata(category, metadata), channels=channels or ["in_app", "push"],
        )
    except Exception:
        logger.exception("%s delivery failed; releasing claim %s", category, dedupe_key)
        db.rollback()
        notification_idempotency.release(db, dedupe_key)
        return None


# --- a new review -> the business's Enterprise Admin(s), and a copy for the Platform Super Admin(s) ----------

# The Super Admin copy of review_submitted covers every module (a Super Admin moderates all of them).
SUBMITTED_PLATFORM_COPY_MODULES = ("training", "product", "service", "event")


def notify_review_submitted(module: str, review_id, *, access_token: str | None = None, resubmitted: bool = False) -> None:
    """Call after a NEW review has been committed, or after the author edited a rejected one (a resubmission). An
    ordinary edit of an existing review is not announced."""
    _dispatch(_deliver_submitted, module, str(review_id), access_token, resubmitted)


def _learner_name(db, module: str, review, item) -> str | None:
    """The reviewer's display name for the notification: the name on their enrolment / registration / review, else
    one identity lookup by user id. Never an e-mail address; None when there is no real name."""
    from sqlalchemy import func

    name = None
    try:
        if module in ("product", "service"):
            name = _clean_name(getattr(review, "reviewer_name", None))
        elif module == "training":
            from app.models.training_model import TrainingEnrolment

            row = db.query(TrainingEnrolment).filter(
                TrainingEnrolment.training_id == review.training_id,
                func.lower(TrainingEnrolment.participant_email) == (review.participant_email or "").strip().lower(),
            ).first()
            name = _clean_name(getattr(row, "participant_name", None))
        elif module == "event":
            from app.models.event_aux_models import EventRegistration

            row = db.query(EventRegistration).filter(
                EventRegistration.event_id == review.event_id,
                func.lower(EventRegistration.participant_email) == (review.participant_email or "").strip().lower(),
            ).first()
            name = _clean_name(getattr(row, "participant_name", None))
    except Exception:
        logger.exception("could not read the reviewer's name from the records")
    user_id = getattr(review, "user_id", None)
    if not name and user_id:
        try:
            from app.services import chat_user_names

            name = _clean_name(chat_user_names.resolve_display_names([user_id]).get(str(user_id)))
        except Exception:
            logger.exception("could not look up the reviewer's name")
    return name


def _deliver_submitted(module: str, review_id: str, access_token: str | None, resubmitted: bool = False):
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
        shown = f'"{name or "your item"}"'
        if resubmitted:
            title, message = "Review resubmitted", f"{stars} review on {shown} was edited and resubmitted. It is waiting for approval."
        else:
            title, message = "New review", f"{stars} review was posted on {shown}."
        metadata = {
            id_key: str(item.id), "entity_type": module, "entity_id": str(item.id), "item_id": str(item.id),
            "item_name": name, "review_id": review_id, "rating": rating, "status": review.moderation_status,
            "learner_name": _learner_name(db, module, review, item), "resubmitted": resubmitted,
        }
        # A resubmission can happen again after the next rejection, so each one gets its own key.
        version = ""
        if resubmitted:
            stamp = getattr(review, "updated_at", None) or review.created_at
            version = f":resubmitted:{stamp.isoformat() if stamp else ''}"
        delivered = _deliver(
            db, recipients=recipients, category="review_submitted", title=title, message=message,
            tenant_id=tenant_id, metadata=metadata, dedupe_key=f"review_submitted:{module}:{review_id}{version}",
        )
        if module in SUBMITTED_PLATFORM_COPY_MODULES:
            # The same notification for the Platform Super Admin(s), once each; anyone already told above
            # (a Super Admin who is also an owner of the business) is not told twice.
            told = set(recipients)
            platform = [uid for uid in recipients_of.resolve_platform_admin_user_ids(access_token) if uid not in told]
            _deliver(
                db, recipients=platform, category="review_submitted", title=title, message=message,
                tenant_id=None, metadata=metadata, dedupe_key=f"review_submitted:{module}:{review_id}{version}:platform",
            )
        return delivered


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


# --- a moderator's action -> the Platform Super Admin(s) -------------------------------------------------
#
#   review_moderated -> an Enterprise Admin / provider approved, rejected or moved a review to pending
#   review_deleted   -> an Enterprise Admin / provider permanently deleted a review
#
# All four modules. Only a Super Admin moderates a review now, so the people told are the Enterprise Admin(s) who own
# the item (never the Super Admin who acted). The branch that told the Super Admins about an Enterprise Admin's action
# is kept for the day such an action is possible again.
# An author deleting their own review is not moderation and is not announced. Everything the notification needs is
# passed in by the caller, because the review of a delete no longer exists once it is committed.

MODERATION_NOTICE_MODULES = ("training", "product", "service", "event")
_ID_KEY = {"training": "training_id", "product": "product_id", "service": "service_id", "event": "event_id"}
_ROLE_LABEL = {"admin": "Enterprise Admin", "provider": "Provider", "super_admin": "Super Admin"}
_STATUS_VERB = {"approved": "approved", "rejected": "rejected", "pending": "moved to pending"}


def _clean_name(value) -> str | None:
    text = " ".join(str(value).split()) if value else ""
    return text if text and "@" not in text else None  # an e-mail address is not a name


def _actor_name(actor: dict | None) -> str | None:
    """The acting person's name: from their token, else one lookup in the identity service. Never an e-mail."""
    name = _clean_name((actor or {}).get("name"))
    if name or not (actor or {}).get("id"):
        return name
    try:
        from app.services import chat_user_names

        return chat_user_names.resolve_display_names([actor["id"]], current_user=actor).get(str(actor["id"]))
    except Exception:
        logger.exception("could not look up the acting moderator's name")
        return None


def notify_review_moderated(
    module: str, *, review_id, item_id, item_name: str | None, tenant_id, previous_status: str | None,
    new_status: str, actor: dict | None, actor_role: str | None, access_token: str | None = None,
) -> None:
    """Call after a moderator changed a review's status and it was committed. Does nothing for a no-op change, for
    a Super Admin acting, or for a module other than training / event."""
    if module not in MODERATION_NOTICE_MODULES or new_status not in _STATUS_VERB or previous_status == new_status:
        return
    _safely_notify(
        "review_moderated", module, review_id=review_id, item_id=item_id, item_name=item_name, tenant_id=tenant_id,
        previous_status=previous_status, new_status=new_status, actor=actor, actor_role=actor_role,
        access_token=access_token,
    )


def notify_review_deleted(
    module: str, *, review_id, item_id, item_name: str | None, tenant_id, previous_status: str | None,
    actor: dict | None, actor_role: str | None, access_token: str | None = None,
) -> None:
    """Call after a moderator (not the author) deleted a review and it was committed."""
    if module not in MODERATION_NOTICE_MODULES:
        return
    _safely_notify(
        "review_deleted", module, review_id=review_id, item_id=item_id, item_name=item_name, tenant_id=tenant_id,
        previous_status=previous_status, new_status="deleted", actor=actor, actor_role=actor_role,
        access_token=access_token,
    )


def _safely_notify(category: str, module: str, **fields) -> None:
    """Queueing the notice can never fail the moderator's action, which is already committed."""
    try:
        _dispatch_platform_notice(category, module, **fields)
    except Exception:
        logger.exception("could not queue the %s notification", category)


def _dispatch_platform_notice(category: str, module: str, *, actor_role, actor, **fields) -> None:
    from datetime import datetime

    # The same change is announced once; a different change to the same review (a later one) gets its own key.
    fields["stamp"] = datetime.utcnow().isoformat()
    _dispatch(
        _deliver_platform_notice, category, module,
        actor={"id": (actor or {}).get("id"), "name": (actor or {}).get("name"), "role": actor_role}, **fields,
    )


def _deliver_platform_notice(
    category: str, module: str, *, review_id, item_id, item_name, tenant_id, previous_status, new_status, actor,
    access_token, stamp,
):
    from app.db.database import SessionLocal
    from app.services import event_notification_service as recipients_of

    with SessionLocal() as db:
        by_super_admin = str(actor.get("role") or "").lower() == "super_admin"
        if by_super_admin:
            # A Super Admin acted on a business's review: the business's own Enterprise Admin(s) are told.
            item = _load_item(db, module, item_id)
            if item is None:
                return None
            owners, owner_tenant = recipients_of.resolve_enterprise_admin_user_ids(db, item, access_token=access_token)
            recipients = [uid for uid in owners if str(uid) != str(actor.get("id"))]
            notice_tenant = owner_tenant
        else:
            # An Enterprise Admin / provider acted: the Platform Super Admin(s) are told.
            recipients = recipients_of.resolve_platform_admin_user_ids(access_token)
            notice_tenant = None
        name = _actor_name(actor)
        role_label = _ROLE_LABEL.get(str(actor.get("role") or "").lower(), "A moderator")
        article = "An" if role_label[:1] in "AEIOU" else "A"
        who = f"{role_label} {name}" if name else (role_label if role_label == "A moderator" else f"{article} {role_label}")
        shown = f'"{item_name}"' if item_name else f"a {module}"
        if category == "review_deleted":
            title, message = "Review deleted", f"{who} deleted a review of {shown}."
        else:
            title, message = "Review moderated", f"{who} {_STATUS_VERB[new_status]} a review of {shown}."
        return _deliver(
            db, recipients=recipients, category=category, title=title, message=message, tenant_id=notice_tenant,
            metadata={
                _ID_KEY[module]: str(item_id), "entity_type": module, "entity_id": str(item_id),
                "item_id": str(item_id), "item_name": item_name, "review_id": str(review_id),
                "previous_status": previous_status, "new_status": new_status, "action": new_status,
                "actor_name": name, "actor_id": str(actor["id"]) if actor.get("id") else None,
                "actor_role": actor.get("role"), "tenant_id": str(tenant_id) if tenant_id else None,
            },
            dedupe_key=f"{category}:{module}:{review_id}:{previous_status}>{new_status}:{stamp}",
            channels=["in_app"],
        )
