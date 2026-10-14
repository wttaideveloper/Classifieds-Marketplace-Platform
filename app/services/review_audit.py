"""The history of review moderation.

`record` is called right before the status change (or the delete) is committed, so the log row and the change
are saved together. A problem writing the log never blocks the moderator: it is logged and the change goes on.
"""
from __future__ import annotations

import logging
from uuid import UUID

import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.review_audit_model import ReviewModerationLog
from app.repository.query_utils import build_pagination_meta
from app.services.review_common import MODERATION_STATUSES

logger = logging.getLogger(__name__)

LOG_MODULES = ("training", "product", "service", "event")
LOG_ACTIONS = (*MODERATION_STATUSES, "deleted")


def _uuid(value) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except (ValueError, TypeError):
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
        from app.services.review_notifications import _learner_name

        return _clean_name(_learner_name(db, module, review, item))
    except Exception:
        logger.exception("could not find the reviewer's name for the moderation history")
        return None


def record(
    db: Session, *, module: str, review_id, item_id, item_name: str | None, tenant_id,
    from_status: str | None, to_status: str, actor: dict | None = None, actor_role: str | None = None,
    customer_name: str | None = None, business_name: str | None = None,
) -> None:
    """Add a history row to the current transaction (the caller commits)."""
    try:
        with db.begin_nested():
            db.add(ReviewModerationLog(
                module=module, review_id=review_id, item_id=item_id, item_name=(item_name or None) and item_name[:255],
                tenant_id=_uuid(tenant_id), from_status=from_status, to_status=to_status,
                actor_user_id=_uuid((actor or {}).get("id")),
                actor_role=(actor_role or (actor or {}).get("role") or None),
                actor_name=_clean_name((actor or {}).get("name")),
                customer_name=_clean_name(customer_name), business_name=_clean_name(business_name),
            ))
    except Exception:
        logger.exception("could not write the review moderation log (%s %s -> %s)", module, review_id, to_status)


def list_log(
    db: Session, access, *, module: str | None = None, review_id: UUID | None = None, item_id: UUID | None = None,
    action: str | None = None, actor_user_id: UUID | None = None, tenant_id: UUID | None = None,
    page: int = 1, page_size: int = 20,
) -> dict:
    """Newest first. An Enterprise Admin sees the history of their own business; a Super Admin sees all."""
    if access is None or access.role == "public":
        raise HTTPException(status_code=401, detail="Not authenticated")
    if access.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Providers are read-only; Enterprise Admin access required")
    if module is not None and module not in LOG_MODULES:
        raise HTTPException(status_code=422, detail=f"module must be one of: {', '.join(LOG_MODULES)}")
    if action is not None and action not in LOG_ACTIONS:
        raise HTTPException(status_code=422, detail=f"action must be one of: {', '.join(LOG_ACTIONS)}")

    query = db.query(ReviewModerationLog)
    if access.role != "super_admin":
        tenant = _uuid(access.tenant_id)
        if tenant_id is not None and tenant_id != tenant:
            raise HTTPException(status_code=403, detail="Not authorized for this tenant")
        query = query.filter(ReviewModerationLog.tenant_id == tenant) if tenant else query.filter(sa.false())
    elif tenant_id is not None:
        query = query.filter(ReviewModerationLog.tenant_id == tenant_id)
    for column, value in (
        (ReviewModerationLog.module, module), (ReviewModerationLog.review_id, review_id),
        (ReviewModerationLog.item_id, item_id), (ReviewModerationLog.to_status, action),
        (ReviewModerationLog.actor_user_id, actor_user_id),
    ):
        if value is not None:
            query = query.filter(column == value)

    total = query.count()
    rows = (
        query.order_by(ReviewModerationLog.created_at.desc(), ReviewModerationLog.id)
        .offset((page - 1) * page_size).limit(page_size).all()
    )
    names = _names_for(rows)
    items = [
        {
            "id": r.id, "module": r.module, "review_id": r.review_id, "item_id": r.item_id, "item_name": r.item_name,
            "from_status": r.from_status, "to_status": r.to_status, "actor_user_id": r.actor_user_id,
            "actor_role": r.actor_role,
            # the name saved when it happened; for older rows, the person's current name
            "actor_name": _clean_name(r.actor_name) or names.get(str(r.actor_user_id)),
            "customer_name": _clean_name(r.customer_name), "business_name": _clean_name(r.business_name),
            "created_at": r.created_at,
        }
        for r in rows
    ]
    return {"items": items, "pagination": build_pagination_meta(total, page, page_size)}


def _names_for(rows) -> dict[str, str | None]:
    """Current display names of the actors of rows that were logged without a name (one cached identity lookup
    each). A failing lookup leaves the name empty; it never fails the history."""
    missing = {r.actor_user_id for r in rows if r.actor_user_id and not _clean_name(r.actor_name)}
    if not missing:
        return {}
    try:
        from app.services import chat_user_names

        return {k: _clean_name(v) for k, v in chat_user_names.resolve_display_names(missing).items()}
    except Exception:
        logger.exception("could not look up the names of the moderators in the review history")
        return {}
