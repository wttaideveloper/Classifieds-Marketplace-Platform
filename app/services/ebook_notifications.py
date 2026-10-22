"""eBook approval notifications, on the existing platform feed and push.

    ebook_submitted          -> the Platform Super Admin(s)   an eBook was sent for approval
    ebook_approved           -> the person who sent it        approved (and `published` says whether it is live)
    ebook_changes_requested  -> the person who sent it        with the reviewer's note in metadata.reason

Sent after the change is committed, from a background thread, and never able to break the API call that triggered it.
"""
from __future__ import annotations

import logging
import threading
from uuid import UUID

from app.services import notification_idempotency

logger = logging.getLogger(__name__)


def _dispatch(fn, *args, **kwargs) -> None:
    def runner():
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.exception("ebook notification delivery failed")

    threading.Thread(target=runner, name="ebook-notification", daemon=True).start()


def _deliver(db, *, recipients, category, title, message, metadata, dedupe_key):
    from app.services.notification_service import create_automatic_notification

    recipients = list(dict.fromkeys(recipients or []))
    if not recipients:
        logger.warning("%s NOT delivered: no recipients (%s)", category, dedupe_key)
        return None
    if not notification_idempotency.claim(db, dedupe_key):
        return None
    payload = {k: v for k, v in {**metadata, "category": category}.items() if v is not None}
    try:
        return create_automatic_notification(db, title=title, message=message, category=category, user_ids=recipients, tenant_id=None,
                                             metadata=payload, channels=["in_app", "push"])
    except Exception:
        logger.exception("%s delivery failed; releasing claim %s", category, dedupe_key)
        db.rollback()
        notification_idempotency.release(db, dedupe_key)
        return None


def notify(ebook_id, outcome: str, *, access_token: str | None = None, reason: str | None = None, published: bool = False) -> None:
    """`outcome`: submitted, approved or changes_requested. Call after the change is committed."""
    _dispatch(_deliver_outcome, str(ebook_id), outcome, access_token, reason, published)


def _deliver_outcome(ebook_id: str, outcome: str, access_token: str | None, reason: str | None, published: bool):
    from app.db.database import SessionLocal
    from app.models.ebook_model import TrainingEbook
    from app.models.training_model import Training
    from app.services import event_notification_service as recipients_of

    with SessionLocal() as db:
        ebook = db.get(TrainingEbook, UUID(ebook_id))
        if ebook is None:
            return None
        training = db.get(Training, ebook.training_id)
        title_of_training = training.title if training else None
        lesson_title = None
        for section in (training.sections if training else None) or []:
            for lesson in section.get("lessons", []) or []:
                if lesson.get("id") == ebook.lesson_id:
                    lesson_title = lesson.get("title")
        shown = f'"{lesson_title or "an eBook"}"' + (f' in "{title_of_training}"' if title_of_training else "")
        meta = {"ebook_id": ebook_id, "entity_type": "ebook", "entity_id": ebook_id, "training_id": str(ebook.training_id), "section_id": ebook.section_id,
                "lesson_id": ebook.lesson_id, "lesson_title": lesson_title, "training_title": title_of_training, "status": ebook.status, "version": ebook.draft_version}
        if outcome == "submitted":
            return _deliver(
                db, recipients=recipients_of.resolve_platform_admin_user_ids(access_token), category="ebook_submitted", title="eBook waiting for approval",
                message=f"The eBook {shown} was sent for approval.", metadata=meta,
                dedupe_key=f"ebook_submitted:{ebook_id}:{ebook.submitted_at.isoformat() if ebook.submitted_at else ''}")
        author = [ebook.submitted_by] if ebook.submitted_by else []
        stamp = ebook.reviewed_at.isoformat() if ebook.reviewed_at else ""
        if outcome == "approved":
            text = f"The eBook {shown} was approved" + (" and is now live for learners." if published else ". It is ready to publish.")
            return _deliver(db, recipients=author, category="ebook_approved", title="eBook approved", message=text,
                            metadata={**meta, "published": published}, dedupe_key=f"ebook_approved:{ebook_id}:{stamp}")
        return _deliver(db, recipients=author, category="ebook_changes_requested", title="Changes requested on your eBook",
                        message=f"Changes were requested on the eBook {shown}.", metadata={**meta, "reason": reason},
                        dedupe_key=f"ebook_changes_requested:{ebook_id}:{stamp}")
