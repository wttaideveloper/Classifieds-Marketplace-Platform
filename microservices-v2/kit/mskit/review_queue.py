"""The review moderation queue across modules (design D6), split between moderation and the review owners.

Copied from app/services/review_admin_service.py. In the monolith one function queried the four review tables
(training, product, service, event) in turn and merged them. Now each owner answers its own slice with the same
query (an ``Adapter`` it registers: catalog -> product/service, learning -> training, events -> event) and the
moderation service merges the slices exactly as the monolith merged its loop:

    counts summed per status, totals summed, rows collected, sorted newest first, then paged.

Moderate / delete run in the owner (its own transaction), same checks and messages.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

import sqlalchemy as sa
from fastapi import HTTPException

from mskit.review_policy import MODERATION_STATUSES, not_deleted, parse_rating, public_name, shown_reason, status_label

MODULES = ("training", "product", "service", "event")
OWNER = {"training": "learning", "product": "catalog", "service": "catalog", "event": "events"}
_ALIASES = {"course": "training", "courses": "training", "trainings": "training", "products": "product",
            "services": "service", "events": "event"}
EVERYONE = {"role": "super_admin", "tenant_id": None, "provider_user_id": None}


def normalize_module(module: str | None) -> str | None:
    if module is None or not str(module).strip():
        return None
    key = str(module).strip().lower()
    key = _ALIASES.get(key, key)
    if key not in MODULES:
        raise HTTPException(status_code=422, detail=f"module must be one of: {', '.join(MODULES)} (course = training)")
    return key


def assert_staff(access) -> None:
    if access is None or access.role == "public":
        raise HTTPException(status_code=401, detail="Not authenticated")
    if access.role not in ("admin", "provider", "super_admin"):
        raise HTTPException(status_code=403, detail="Not authorized")


def scope_for_business(access, tenant_id):
    if tenant_id is None:
        return access
    if access.role == "super_admin":
        return SimpleNamespace(role="admin", tenant_id=tenant_id, provider_user_id=None)
    if str(access.tenant_id).lower() != str(tenant_id).lower():
        raise HTTPException(status_code=403, detail="Not authorized for this tenant")
    return access


def access_to_dict(access) -> dict:
    return {"role": access.role, "tenant_id": str(access.tenant_id) if getattr(access, "tenant_id", None) else None,
            "provider_user_id": str(access.provider_user_id) if getattr(access, "provider_user_id", None) else None}


def access_from_dict(data: dict):
    return SimpleNamespace(role=data["role"], tenant_id=UUID(data["tenant_id"]) if data.get("tenant_id") else None,
                           provider_user_id=UUID(data["provider_user_id"]) if data.get("provider_user_id") else None)


def row(module: str, review, item, name_column_value, names: dict) -> dict:
    enterprise = getattr(item, "enterprise", None)
    base = {
        "module": module,
        "review_id": review.id,
        "item_id": item.id,
        "item_name": name_column_value,
        "rating": parse_rating(review.rating),
        "comment": review.comment,
        "moderation_status": review.moderation_status or "pending",
        "status_label": status_label(review.moderation_status),
        "reason": shown_reason(review.moderation_status, getattr(review, "moderation_reason", None)),
        "created_at": review.created_at,
        "updated_at": getattr(review, "updated_at", None) or review.created_at,
        "tenant_id": (enterprise.tenant_id if enterprise is not None else None) or getattr(item, "tenant_id", None),
        "business_name": getattr(enterprise, "business_short_name", None) if enterprise is not None else None,
    }
    if module == "training":
        base.update(reviewer_name=names.get((review.training_id, review.participant_email)),
                    reviewer_email=review.participant_email, reviewer_user_id=None, is_verified=True)
    elif module == "event":
        base.update(reviewer_name=names.get((review.event_id, (review.participant_email or "").strip().lower())),
                    reviewer_email=review.participant_email, reviewer_user_id=review.user_id, is_verified=True)
    else:
        base.update(reviewer_name=public_name(review.reviewer_name), reviewer_email=None,
                    reviewer_user_id=review.user_id, is_verified=bool(review.is_verified_purchase))
    return base


def _json(value):
    if isinstance(value, dict):
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


# ---------------------------------------------------------------- owner side

class Adapter:
    """What an owner provides for each of its modules (copied branches of the monolith functions)."""

    modules: tuple = ()

    def scoped_query(self, db, module, access):  # -> (query, Review, Item, fk, name_col)
        raise NotImplementedError

    def names(self, db, module, rows) -> dict:
        return {}

    def moderate(self, db, module, review_id, action, access, *, access_token, actor, reason) -> None:
        raise NotImplementedError

    def delete(self, db, module, review_id, current_user, *, access_token, reason) -> dict:
        raise NotImplementedError


def owner_slice(db, adapter: Adapter, module, scope, *, status, item_id, rating, q, limit) -> dict:
    """One module's part of list_reviews_for_moderation (the body of the monolith's loop)."""
    counts = {s: 0 for s in MODERATION_STATUSES}
    query, Review, Item, fk, name_col = adapter.scoped_query(db, module, scope)
    query = query.filter(not_deleted(Review.moderation_status))
    if item_id is not None:
        query = query.filter(fk == item_id)
    if rating is not None:
        query = query.filter(Review.rating == str(rating))
    if q and q.strip():
        query = query.filter(Review.comment.ilike(f"%{q.strip()}%"))
    for review_status, n in query.with_entities(Review.moderation_status, sa.func.count(Review.id)).group_by(Review.moderation_status).all():
        counts[review_status if review_status in counts else "pending"] += n
    listed = query
    if status is not None:
        listed = listed.filter(Review.moderation_status == status if status != "pending"
                               else sa.or_(Review.moderation_status == "pending", Review.moderation_status.is_(None)))
    total = listed.order_by(None).with_entities(sa.func.count(Review.id)).scalar() or 0
    pairs = listed.order_by(Review.created_at.desc(), Review.id).limit(limit).all()
    names = adapter.names(db, module, pairs)
    rows = [_json(row(module, r, i, getattr(i, name_col.key, None), names)) for r, i in pairs]
    return {"counts": counts, "total": total, "rows": rows}


# Request bodies at module level: with `from __future__ import annotations` FastAPI cannot resolve models defined
# inside the router function and would read them as query parameters.
from pydantic import BaseModel  # noqa: E402


class SliceBody(BaseModel):
    module: str
    scope: dict
    status: str | None = None
    item_id: UUID | None = None
    rating: int | None = None
    q: str | None = None
    limit: int

class ModerateBody(BaseModel):
    action: str
    access: dict
    actor: dict | None = None
    access_token: str | None = None
    reason: str | None = None

class DeleteBody(BaseModel):
    current_user: dict
    access_token: str | None = None
    reason: str | None = None


def owner_router(adapter: Adapter, get_db):
    from fastapi import APIRouter, Depends

    from mskit.app import require_internal_key

    router = APIRouter(dependencies=[Depends(require_internal_key)], tags=["internal"])

    @router.post("/reviews/moderation-slice")
    def slice_(body: SliceBody, db=Depends(get_db)):
        return owner_slice(db, adapter, body.module, access_from_dict(body.scope), status=body.status, item_id=body.item_id,
                           rating=body.rating, q=body.q, limit=body.limit)

    @router.post("/reviews/{module}/{review_id}/moderate")
    def moderate(module: str, review_id: UUID, body: ModerateBody, db=Depends(get_db)):
        access = access_from_dict(body.access)
        adapter.moderate(db, module, review_id, body.action, access, access_token=body.access_token, actor=body.actor, reason=body.reason)
        # re-read through the list query (same shape, same scope; unscoped if the scoped lookup misses it)
        for scope in (access, access_from_dict(EVERYONE)):
            query, Review, Item, fk, name_col = adapter.scoped_query(db, module, scope)
            pair = query.filter(Review.id == review_id).first()
            if pair is not None:
                break
        else:
            raise HTTPException(status_code=404, detail="Review not found")
        review, item = pair
        return _json(row(module, review, item, getattr(item, name_col.key, None), adapter.names(db, module, [pair])))

    @router.post("/reviews/{module}/{review_id}/delete")
    def delete(module: str, review_id: UUID, body: DeleteBody, db=Depends(get_db)):
        return adapter.delete(db, module, review_id, body.current_user, access_token=body.access_token, reason=body.reason)

    return router


# ---------------------------------------------------------------- moderation side

def _owner_call(module: str, method: str, path: str, json: dict):
    from mskit import internal
    from mskit.clients import _raise_passthrough

    r = internal.call(OWNER[module], method, path, json=json, expect=range(200, 600), timeout=30.0)
    _raise_passthrough(r)
    return r.json()


def list_reviews_for_moderation(access, *, module=None, status=None, item_id=None, rating=None, q=None, tenant_id=None, page=1, page_size=20) -> dict:
    from mskit.review_pagination import build_pagination_meta

    assert_staff(access)
    wanted_module = normalize_module(module)
    scope = scope_for_business(access, tenant_id)
    if status is not None and status not in MODERATION_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of: {', '.join(MODERATION_STATUSES)}")
    if rating is not None and not 1 <= rating <= 5:
        raise HTTPException(status_code=422, detail="rating must be a whole number from 1 to 5")
    counts = {s: 0 for s in MODERATION_STATUSES}
    total = 0
    collected: list[dict] = []
    for current in ([wanted_module] if wanted_module else list(MODULES)):
        part = _owner_call(current, "POST", "/reviews/moderation-slice", {
            "module": current, "scope": access_to_dict(scope), "status": status, "item_id": str(item_id) if item_id else None,
            "rating": rating, "q": q, "limit": page * page_size})
        for k, n in part["counts"].items():
            counts[k] += n
        total += part["total"]
        collected.extend(part["rows"])
    collected.sort(key=lambda r: (r["created_at"], str(r["review_id"])), reverse=True)
    start = (page - 1) * page_size
    return {"items": collected[start:start + page_size], "counts": counts, "pagination": build_pagination_meta(total, page, page_size)}


def moderate_any_review(module, review_id, action, access, *, access_token=None, actor=None, reason=None) -> dict:
    from mskit.review_policy import validate_action

    validate_action(action)
    current = normalize_module(module)
    if current is None:
        raise HTTPException(status_code=422, detail=f"module must be one of: {', '.join(MODULES)} (course = training)")
    if access is None or access.role == "public":
        raise HTTPException(status_code=401, detail="Not authenticated")
    return _owner_call(current, "POST", f"/reviews/{current}/{review_id}/moderate",
                       {"action": action, "access": access_to_dict(access), "actor": actor, "access_token": access_token, "reason": reason})


def delete_any_review(module, review_id, current_user, *, access_token=None, reason=None) -> dict:
    current = normalize_module(module)
    if current is None:
        raise HTTPException(status_code=422, detail=f"module must be one of: {', '.join(MODULES)} (course = training)")
    return _owner_call(current, "POST", f"/reviews/{current}/{review_id}/delete",
                       {"current_user": current_user, "access_token": access_token, "reason": reason})
