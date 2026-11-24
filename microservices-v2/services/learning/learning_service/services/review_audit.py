"""Learning's side of the review moderation history (the history itself belongs to the moderation service).

Same functions as app/services/review_audit.py. ``business_name_of`` / ``customer_name_of`` read catalog's own
data; ``record`` sends the row to moderation after the caller's transaction commits (monolith: added to that
transaction; any error swallowed and logged - unchanged).
"""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from mskit import clients

logger = logging.getLogger(__name__)


def _uuid(value) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def _clean_name(value) -> str | None:
    text = " ".join(str(value).split()) if value else ""
    return text[:255] if text and "@" not in text else None  # an e-mail address is not a name


def business_name_of(item) -> str | None:
    """The owning enterprise's short name, for the history."""
    enterprise = getattr(item, "enterprise", None)
    return _clean_name(getattr(enterprise, "business_short_name", None)) if enterprise is not None else None


def customer_name_of(db: Session, module: str, review, item) -> str | None:
    """The reviewer's display name for the history (never an e-mail address)."""
    try:
        from learning_service.services.review_notifications import _learner_name

        return _clean_name(_learner_name(db, module, review, item))
    except Exception:
        logger.exception("could not find the reviewer's name for the moderation history")
        return None


def record(
    db: Session, *, module: str, review_id, item_id, item_name: str | None, tenant_id,
    from_status: str | None, to_status: str, actor: dict | None = None, actor_role: str | None = None,
    customer_name: str | None = None, business_name: str | None = None, reason: str | None = None,
) -> None:
    try:
        clients.record_review_moderation(
            db, module=module, review_id=review_id, item_id=item_id, item_name=(item_name or None) and item_name[:255],
            tenant_id=_uuid(tenant_id), from_status=from_status, to_status=to_status,
            actor_user_id=_uuid((actor or {}).get("id")),
            actor_role=(actor_role or (actor or {}).get("role") or None),
            actor_name=_clean_name((actor or {}).get("name")),
            customer_name=_clean_name(customer_name), business_name=_clean_name(business_name),
            reason=(reason or None) and reason[:500],
        )
    except Exception:
        logger.exception("could not write the review moderation log (%s %s -> %s)", module, review_id, to_status)
