"""Blog / news notifications, on the existing platform feed and the generic Socket.IO ``notification`` event.

    blog_post_submitted          -> the Platform Super Admin(s)   a merchant sent a post (or an edit of a live post) for approval
    blog_post_approved           -> the post's author             a Super Admin approved it after review
    blog_post_published          -> the post's author             a Super Admin published it directly, without a review step
    blog_post_changes_requested  -> the post's author             (metadata.reason)
    blog_post_rejected           -> the post's author             (metadata.reason)
    blog_post_unpublished        -> the post's author             a Super Admin took a live post down (metadata.reason)
    blog_post_deleted            -> the post's author             a Super Admin deleted it (metadata.reason)

Every one carries: post_id, entity_type, entity_id, kind (the post type), title, slug, status, enterprise_id and business_name,
author_id and author_name, tenant_id, reason (when there is one), occurred_at (UTC, ISO 8601) and deep_link, the page that
opens the post for that reader: the Super Admin's page for blog_post_submitted, the Enterprise Admin's page for the rest
(BLOG_DEEP_LINK_SUPER_ADMIN / BLOG_DEEP_LINK_ENTERPRISE).

Sent after the change is committed, from a background thread, and never able to break the API call that triggered it.
Each delivery is claimed first with a unique key (notification_idempotency), so a retry cannot send it twice. The reason
is in the metadata for the app to show; like review reasons it is left out of the push payload.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from uuid import UUID

from app.core.config import settings
from app.services import notification_idempotency

logger = logging.getLogger(__name__)

DECISION_CATEGORY = {
    "approved": "blog_post_approved",
    "published": "blog_post_published",
    "changes_requested": "blog_post_changes_requested",
    "rejected": "blog_post_rejected",
    "unpublished": "blog_post_unpublished",
    "deleted": "blog_post_deleted",
}


def _dispatch(fn, *args, **kwargs) -> None:
    def runner():
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.exception("blog notification delivery failed")

    threading.Thread(target=runner, name="blog-notification", daemon=True).start()


def _deliver(db, *, recipients, category, title, message, metadata, dedupe_key):
    from app.services.notification_service import create_automatic_notification

    if not recipients:
        logger.warning("%s NOT delivered: no recipients (%s)", category, dedupe_key)
        return None
    if not notification_idempotency.claim(db, dedupe_key):
        logger.info("%s skipped: already delivered (%s)", category, dedupe_key)
        return None
    payload = {k: v for k, v in {**metadata, "category": category}.items() if v is not None}
    try:
        return create_automatic_notification(
            db, title=title, message=message, category=category, user_ids=list(recipients), tenant_id=None,
            metadata=payload, channels=["in_app", "push"],
        )
    except Exception:
        logger.exception("%s delivery failed; releasing claim %s", category, dedupe_key)
        db.rollback()
        notification_idempotency.release(db, dedupe_key)
        return None


def deep_link(role: str, post_id) -> str:
    """Where the post opens for this reader: "super_admin" or "enterprise"."""
    template = settings.BLOG_DEEP_LINK_SUPER_ADMIN if role == "super_admin" else settings.BLOG_DEEP_LINK_ENTERPRISE
    return template.replace("{post_id}", str(post_id))


def _metadata(post, status: str, *, reader: str, **extra) -> dict:
    return {
        "post_id": str(post.id), "entity_type": "blog_post", "entity_id": str(post.id), "kind": post.kind, "post_type": post.kind,
        "title": post.title, "slug": post.slug, "status": status, "business_name": post.business_name,
        "enterprise_id": str(post.enterprise_id) if post.enterprise_id else None,
        "tenant_id": str(post.tenant_id) if post.tenant_id else None,
        "author_id": str(post.author_id) if post.author_id else None, "author_name": post.author_name,
        "occurred_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z", "deep_link": deep_link(reader, post.id), **extra,
    }


def _stamp(value) -> str:
    return value.isoformat() if value else ""


# --- a merchant sent a post for approval -> the Super Admin(s) ---------------------------------------------------

def notify_submitted(post_id, *, access_token: str | None = None, is_edit: bool = False) -> None:
    """Call after the submission has been committed. `is_edit` is an edit of a post that is already live."""
    _dispatch(_deliver_submitted, str(post_id), access_token, is_edit)


def _deliver_submitted(post_id: str, access_token: str | None, is_edit: bool):
    from app.db.database import SessionLocal
    from app.models.blog_model import BlogPost
    from app.services import event_notification_service as recipients_of

    with SessionLocal() as db:
        post = db.get(BlogPost, UUID(post_id))
        if post is None or post.status != "pending_approval":
            return None     # decided already; the decision announces itself
        who = post.business_name or post.author_name or "A merchant"
        kind = "news item" if post.kind == "news" else "blog post"
        if is_edit:
            title, message = "Post edit waiting for approval", f'{who} edited the {kind} "{post.title}". The new version is waiting for approval.'
        else:
            title, message = "New post waiting for approval", f'{who} sent the {kind} "{post.title}" for approval.'
        recipients = recipients_of.resolve_platform_admin_user_ids(access_token)
        return _deliver(
            db, recipients=recipients, category="blog_post_submitted", title=title, message=message,
            metadata=_metadata(post, "pending_approval", reader="super_admin", is_edit=is_edit),
            dedupe_key=f"blog_post_submitted:{post_id}:{_stamp(post.submitted_at)}",
        )


# --- a Super Admin's decision -> the author ----------------------------------------------------------------------

def notify_decision(post_id, outcome: str, *, reason: str | None = None) -> None:
    """Call after a Super Admin's action on a merchant's post has been committed. `outcome` is approved, published,
    changes_requested, rejected, unpublished or deleted."""
    if outcome in DECISION_CATEGORY:
        _dispatch(_deliver_decision, str(post_id), outcome, reason)


def _deliver_decision(post_id: str, outcome: str, reason: str | None):
    from app.db.database import SessionLocal
    from app.models.blog_model import BlogPost

    with SessionLocal() as db:
        post = db.get(BlogPost, UUID(post_id))
        if post is None or post.source != "merchant" or post.author_id is None:
            return None
        shown = f'"{post.title}"'
        title, message = {
            "approved": ("Post approved", f"Your post {shown} was approved and is now live."),
            "published": ("Post published", f"Your post {shown} was published."),
            "changes_requested": ("Changes requested", f"Changes were requested on your post {shown}."),
            "rejected": ("Post not approved", f"Your post {shown} was not approved."),
            "unpublished": ("Post taken down", f"Your post {shown} was taken down."),
            "deleted": ("Post deleted", f"Your post {shown} was deleted."),
        }[outcome]
        category = DECISION_CATEGORY[outcome]
        version = _stamp(post.reviewed_at) or _stamp(post.deleted_at) or _stamp(post.updated_at)
        return _deliver(
            db, recipients=[post.author_id], category=category, title=title, message=message,
            metadata=_metadata(post, post.status, reader="enterprise", reason=reason),
            dedupe_key=f"{category}:{post_id}:{version}",
        )
