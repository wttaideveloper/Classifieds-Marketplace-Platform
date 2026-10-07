"""Training approval + enrollment notifications addressed to administrators.

    training_submitted            -> active Platform / Super Admins     (submitted or resubmitted)
    training_approved|rejected|
    training_changes_requested    -> the owning Enterprise Admin(s)     (after the Super Admin decision)
    training_enrolled             -> the owning Enterprise Admin(s)     (learner's enrollment confirmed)

Mirrors the Event approval workflow (event_notification_service) and reuses its recipient
resolvers, so Events and Trainings agree on who a "Platform Admin" and an "Enterprise Admin" are.
Everything rides the existing platform feed (notifications / user_notifications), the generic
Socket.IO ``notification`` event and push — no separate channel.

Callers invoke these *after* the state change is committed. Recipients are derived from the saved
Training / enrollment records inside the background job, and every delivery is claimed first with a
unique key (notification_idempotency) so retries and concurrent workers cannot send it twice.
"""
import logging
from decimal import Decimal, InvalidOperation
from uuid import UUID

from app.services import notification_idempotency, training_notifications

logger = logging.getLogger(__name__)

APPROVAL_NOTIFICATION_TYPES = {
    "pending_approval": "training_submitted",
    "approved": "training_approved",
    "rejected": "training_rejected",
    "needs_revision": "training_changes_requested",
}

_APPROVAL_TEXT = {
    "training_submitted": ("Training submitted for approval", '"{title}" was submitted for approval.'),
    "training_approved": ("Training approved", '"{title}" has been approved.'),
    "training_rejected": ("Training rejected", '"{title}" has been rejected.'),
    "training_changes_requested": ("Training changes requested", 'Changes were requested for "{title}".'),
}


def is_paid(training) -> bool:
    try:
        return Decimal(str(training.price or 0)) > 0
    except (InvalidOperation, ValueError):
        return False


def payment_satisfied(db, training, participant_email: str) -> bool:
    """Free trainings need no payment. A paid training counts as paid only once an order for this
    learner is recorded with a confirmed payment (checkout is the only path that creates one)."""
    if not is_paid(training):
        return True
    from app.models.training_model import TrainingOrder

    return db.query(TrainingOrder).filter(
        TrainingOrder.training_id == training.id,
        TrainingOrder.participant_email == participant_email,
        TrainingOrder.payment_status == "confirmed",
        TrainingOrder.status.in_(("confirmed", "completed")),
    ).first() is not None


def _deliver_to_users(db, *, recipients, notification_type, title, message, tenant_id, metadata, dedupe_key):
    from app.services.notification_service import create_automatic_notification

    if not recipients:
        from app.core.config import settings

        logger.warning(
            "%s NOT delivered: no eligible recipients (%s; invigorate_internal_api_configured=%s). "
            "GET /api/v1/admin/notifications/diagnostics shows what resolved.",
            notification_type, dedupe_key, settings.invigorate_internal_api_configured,
        )
        return None
    if not notification_idempotency.claim(db, dedupe_key):
        logger.info("%s skipped: already delivered (%s)", notification_type, dedupe_key)
        return None
    try:
        return create_automatic_notification(
            db, title=title, message=message, category=notification_type, user_ids=recipients,
            tenant_id=tenant_id, metadata=training_notifications._routing_metadata(notification_type, metadata),
            channels=["in_app", "push"],
        )
    except Exception:
        logger.exception("%s delivery failed; releasing claim %s", notification_type, dedupe_key)
        db.rollback()
        notification_idempotency.release(db, dedupe_key)
        return None


# --- training approval --------------------------------------------------------------------

def notify_training_approval(training, *, reason: str | None = None, access_token: str | None = None) -> None:
    """Call after a Training's status has been committed as pending_approval / approved / rejected /
    needs_revision. `training.moderation_history` must already include this transition: its length
    is the idempotency discriminator, so a resubmission (a new history entry) notifies again while a
    retry of the same transition does not."""
    if APPROVAL_NOTIFICATION_TYPES.get(training.status) is None:
        return
    training_notifications._dispatch(
        _deliver_approval,
        training_id=str(training.id), status=training.status, reason=reason,
        history_index=len(training.moderation_history or []), access_token=access_token,
    )


def _deliver_approval(*, training_id: str, status: str, reason: str | None, history_index: int, access_token: str | None = None):
    from app.db.database import SessionLocal
    from app.models.training_model import Training
    from app.services import event_notification_service as recipients_of

    notification_type = APPROVAL_NOTIFICATION_TYPES[status]
    with SessionLocal() as db:
        training = db.get(Training, UUID(training_id))
        if training is None:
            return None
        if status == "pending_approval":
            recipients, tenant_id = recipients_of.resolve_platform_admin_user_ids(access_token=access_token), None
        else:
            recipients, tenant_id = recipients_of.resolve_enterprise_admin_user_ids(db, training, access_token=access_token)
        title, message = _APPROVAL_TEXT[notification_type]
        return _deliver_to_users(
            db, recipients=recipients, notification_type=notification_type,
            title=title, message=message.format(title=training.title), tenant_id=tenant_id,
            metadata={
                "training_id": training_id, "entity_type": "training", "entity_id": training_id,
                "status": status,
                "reason": reason if status in ("rejected", "needs_revision") else None,
            },
            dedupe_key=f"{notification_type}:{training_id}:{history_index}",
        )


# --- enrollment confirmed -> Enterprise Admin ---------------------------------------------

def notify_enrollment_confirmed_to_admins(training, enrolment, *, actor_id=None, access_token: str | None = None) -> None:
    """Call after an enrollment is saved as `enrolled`: automatic acceptance, payment success,
    waitlist promotion, or an Enterprise Admin's own acceptance (the acting admin is excluded).
    Sent once per enrollment, and only when the training is free or its payment is recorded."""
    training_notifications._dispatch(
        _deliver_enrolled,
        training_id=str(training.id), enrolment_id=str(enrolment.id),
        actor_id=str(actor_id) if actor_id else None, access_token=access_token,
    )


def _deliver_enrolled(*, training_id: str, enrolment_id: str, actor_id: str | None, access_token: str | None = None):
    from app.db.database import SessionLocal
    from app.models.training_model import Training, TrainingEnrolment
    from app.services import event_notification_service as recipients_of

    with SessionLocal() as db:
        training = db.get(Training, UUID(training_id))
        enrolment = db.get(TrainingEnrolment, UUID(enrolment_id))
        if training is None or enrolment is None or enrolment.status != "enrolled":
            return None
        if not payment_satisfied(db, training, enrolment.participant_email):
            logger.info("training_enrolled held back for %s: payment not recorded", enrolment_id)
            return None
        recipients, tenant_id = recipients_of.resolve_enterprise_admin_user_ids(db, training, access_token=access_token)
        recipients = [uid for uid in recipients if str(uid) != actor_id]
        return _deliver_to_users(
            db, recipients=recipients, notification_type="training_enrolled",
            title="New enrollment", message=f'A learner enrolled in "{training.title}".',
            tenant_id=tenant_id,
            metadata={
                "training_id": training_id, "entity_type": "training", "entity_id": training_id,
                "enrollment_id": enrolment_id, "status": "enrolled",
            },
            dedupe_key=f"training_enrolled:{enrolment_id}",
        )
