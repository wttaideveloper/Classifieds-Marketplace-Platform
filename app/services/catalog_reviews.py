"""Product and service reviews. The two modules are identical except for the table, so they share this code.

    submit / edit   one review per user per item; a new review is pending, an edit keeps its status
    public list     approved reviews only; the average is null when there are none
    my review       the caller's own review, whatever its status
    delete          the author, or staff of the business that owns the item (Super Admin: any)
    moderate        Enterprise Admin of the owning business, or a Super Admin
    cards           approved average + count for a page of items (one query), and the latest reviews for a detail
"""
from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services import review_audit, review_notifications
from app.services.review_access import assert_can_moderate, assert_staff_in_tenant
from app.services.review_common import (
    ListOptions,
    apply_list_options,
    average_rating,
    clean_comment,
    parse_rating,
    public_name,
    rating_distribution,
    validate_action,
)


def _config(kind: str):
    if kind == "product":
        from app.models.product_model import ProductReview

        return ProductReview, "product_id", "Product"
    if kind == "service":
        from app.models.service_model import ServiceReview

        return ServiceReview, "service_id", "Service"
    raise ValueError(kind)


def _fk(kind: str):
    model, fk, _ = _config(kind)
    return model, getattr(model, fk), fk


def identity(current_user: dict) -> tuple[UUID, str | None]:
    """(user id, a name that may be shown). An email address is never stored as the reviewer's name."""
    user_id = (current_user or {}).get("id")
    try:
        parsed = UUID(str(user_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Authenticated user required")
    return parsed, public_name(current_user.get("name"))


def _owner_tenants(item) -> tuple:
    return (item.enterprise.tenant_id if getattr(item, "enterprise", None) else None, getattr(item, "tenant_id", None))


def _item_name(kind: str, item) -> str | None:
    return getattr(item, "product_name" if kind == "product" else "service_name", None)


def _log(db: Session, kind: str, item, review, to_status: str, actor, actor_role) -> None:
    review_audit.record(
        db, module=kind, review_id=review.id, item_id=item.id, item_name=_item_name(kind, item),
        tenant_id=next((t for t in _owner_tenants(item) if t), None),
        from_status=review.moderation_status, to_status=to_status, actor=actor, actor_role=actor_role,
    )


def upsert_review(db: Session, kind: str, item, data, current_user: dict, *, is_verified: bool, access_token: str | None = None):
    model, fk_col, fk = _fk(kind)
    user_id, reviewer_name = identity(current_user)
    comment = clean_comment(data.comment)

    existing = db.query(model).filter(fk_col == item.id, model.user_id == user_id).first()
    if existing:
        existing.rating = str(data.rating)
        existing.comment = comment
        existing.reviewer_name = reviewer_name
        existing.is_verified_purchase = is_verified
        db.commit()
        db.refresh(existing)
        return existing

    review = model(**{fk: item.id}, user_id=user_id, reviewer_name=reviewer_name, rating=str(data.rating),
                   comment=comment, is_verified_purchase=is_verified, moderation_status="pending")
    db.add(review)
    try:
        db.commit()
    except IntegrityError:
        # Two first posts from the same user at the same moment: the other one won. Update that review instead.
        db.rollback()
        existing = db.query(model).filter(fk_col == item.id, model.user_id == user_id).first()
        if existing is None:
            raise
        existing.rating = str(data.rating)
        existing.comment = comment
        existing.reviewer_name = reviewer_name
        existing.is_verified_purchase = is_verified
        db.commit()
        db.refresh(existing)
        return existing
    db.refresh(review)
    review_notifications.notify_review_submitted(kind, review.id, access_token=access_token)
    return review


def list_approved(db: Session, kind: str, item_id: UUID, opts: ListOptions | None = None) -> dict:
    """Approved reviews. The average, total and star breakdown cover all of them; `opts` only changes which are listed."""
    model, fk_col, _ = _fk(kind)
    rows = [
        r for r in db.query(model).filter(fk_col == item_id, model.moderation_status == "approved")
        .order_by(model.created_at.desc()).all()
        if parse_rating(r.rating) is not None
    ]
    ratings = [parse_rating(r.rating) for r in rows]
    shown, pagination = apply_list_options(
        rows, opts or ListOptions(),
        rating_of=lambda r: parse_rating(r.rating), created_of=lambda r: r.created_at, comment_of=lambda r: r.comment,
    )
    return {
        "reviews": shown,
        "average_rating": average_rating(ratings),
        "total": len(rows),
        "rating_distribution": rating_distribution(ratings),
        "pagination": pagination,
    }


def my_review(db: Session, kind: str, item_id: UUID, current_user: dict):
    model, fk_col, _ = _fk(kind)
    user_id, _ = identity(current_user)
    return db.query(model).filter(fk_col == item_id, model.user_id == user_id).first()


def delete_review(db: Session, kind: str, item, review_id: UUID, current_user: dict, *, access_token: str | None = None):
    model, fk_col, _ = _fk(kind)
    user_id, _ = identity(current_user)
    review = db.query(model).filter(model.id == review_id, fk_col == item.id).first()
    if not review:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
    if review.user_id != user_id:
        role = str((current_user or {}).get("role") or "").lower()
        if role not in ("admin", "provider", "super_admin"):
            raise HTTPException(status_code=403, detail="You can only delete your own review")
        # Staff may delete other people's reviews, but only on items their own business owns.
        assert_staff_in_tenant(db, current_user, access_token, *_owner_tenants(item))
        _log(db, kind, item, review, "deleted", current_user, role)   # a moderator removed someone else's review
    db.delete(review)
    db.commit()
    return {"message": "Review deleted"}


def moderate_review(db: Session, kind: str, item, review_id: UUID, action: str, access, *, access_token: str | None = None, actor: dict | None = None):
    model, fk_col, _ = _fk(kind)
    validate_action(action)
    assert_can_moderate(access, *_owner_tenants(item))
    # The review must belong to the item in the URL, not just exist somewhere.
    review = db.query(model).filter(model.id == review_id, fk_col == item.id).first()
    if not review:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
    previous = review.moderation_status
    if previous != action:
        _log(db, kind, item, review, action, actor, access.role)
    review.moderation_status = action
    db.commit()
    db.refresh(review)
    if previous != action:
        review_notifications.notify_review_decision(kind, review.id, action, access_token=access_token)
    return review


# --- cards and detail pages ---------------------------------------------------------------------------

def approved_stats(db: Session, kind: str, ids: list) -> dict:
    """{item id: (average or None, approved count)} for a page of items, in one query."""
    if not ids:
        return {}
    model, fk_col, fk = _fk(kind)
    by_item: dict = {}
    for item_id, rating in db.query(fk_col, model.rating).filter(
        fk_col.in_(ids), model.moderation_status == "approved"
    ).all():
        value = parse_rating(rating)
        if value is not None:
            by_item.setdefault(item_id, []).append(value)
    return {i: (average_rating(r), len(r)) for i, r in by_item.items()}


def catalog_reviews(db: Session, kind: str, item_id: UUID, limit: int = 20) -> tuple[list[dict], int, float | None]:
    """(latest approved reviews in the catalog-review shape, approved count, average) for a detail page."""
    model, fk_col, _ = _fk(kind)
    rows = [
        r for r in db.query(model).filter(fk_col == item_id, model.moderation_status == "approved")
        .order_by(model.created_at.desc()).all()
        if parse_rating(r.rating) is not None
    ]
    shown = [
        {"id": str(r.id), "rating": parse_rating(r.rating), "comment": r.comment,
         "reviewer_name": public_name(r.reviewer_name), "created_at": r.created_at}
        for r in rows[:limit]
    ]
    return shown, len(rows), average_rating(parse_rating(r.rating) for r in rows)
