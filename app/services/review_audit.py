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


def record(
    db: Session, *, module: str, review_id, item_id, item_name: str | None, tenant_id,
    from_status: str | None, to_status: str, actor: dict | None = None, actor_role: str | None = None,
) -> None:
    """Add a history row to the current transaction (the caller commits)."""
    try:
        with db.begin_nested():
            db.add(ReviewModerationLog(
                module=module, review_id=review_id, item_id=item_id, item_name=(item_name or None) and item_name[:255],
                tenant_id=_uuid(tenant_id), from_status=from_status, to_status=to_status,
                actor_user_id=_uuid((actor or {}).get("id")),
                actor_role=(actor_role or (actor or {}).get("role") or None),
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
    return {"items": rows, "pagination": build_pagination_meta(total, page, page_size)}
