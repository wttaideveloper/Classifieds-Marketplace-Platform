"""Product and service reviews. The two modules are identical except for the table, so they share this code.

    submit / edit   one review per user per item; a new review is approved and public at once, an edit keeps its
                    status, except that editing a rejected review sends it back to pending (a resubmission)
    public list     approved reviews only; the average is null when there are none
    my review       the caller's own review, whatever its status
    delete          the author, or a Super Admin
    moderate        a Super Admin only; Enterprise Admins can look but not change
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
    NEW_REVIEW_STATUS,
    check_move,
    public_name,
    rating_distribution,
    status_after_edit,
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
        customer_name=review_audit.customer_name_of(db, kind, review, item), business_name=review_audit.business_name_of(item),
    )


def _edit(db: Session, kind: str, existing, data, comment, reviewer_name, is_verified, access_token):
    """Change the author's own review. The status stays, except that a rejected review goes back to pending."""
    was_rejected = existing.moderation_status == "rejected"
    existing.rating = str(data.rating)
    existing.comment = comment
    existing.reviewer_name = reviewer_name
    existing.is_verified_purchase = is_verified
    existing.moderation_status = status_after_edit(existing.moderation_status)
    db.commit()
    db.refresh(existing)
    if was_rejected:
        review_notifications.notify_review_submitted(kind, existing.id, access_token=access_token, resubmitted=True)
    return existing


def upsert_review(db: Session, kind: str, item, data, current_user: dict, *, is_verified: bool, access_token: str | None = None):
    model, fk_col, fk = _fk(kind)
    user_id, reviewer_name = identity(current_user)
    comment = clean_comment(data.comment)

    existing = db.query(model).filter(fk_col == item.id, model.user_id == user_id).first()
    if existing:
        return _edit(db, kind, existing, data, comment, reviewer_name, is_verified, access_token)

    review = model(**{fk: item.id}, user_id=user_id, reviewer_name=reviewer_name, rating=str(data.rating),
                   comment=comment, is_verified_purchase=is_verified, moderation_status=NEW_REVIEW_STATUS)
    db.add(review)
    try:
        db.commit()
    except IntegrityError:
        # Two first posts from the same user at the same moment: the other one won. Update that review instead.
        db.rollback()
        existing = db.query(model).filter(fk_col == item.id, model.user_id == user_id).first()
        if existing is None:
            raise
        return _edit(db, kind, existing, data, comment, reviewer_name, is_verified, access_token)
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
    announce = None  # set when a moderator (not the author) deletes it: the owner's Enterprise Admins are told
    if review.user_id != user_id:
        role = str((current_user or {}).get("role") or "").lower()
        if role != "super_admin":
            raise HTTPException(status_code=403, detail="You can only delete your own review")
        assert_staff_in_tenant(db, current_user, access_token, *_owner_tenants(item))  # an active Super Admin only
        _log(db, kind, item, review, "deleted", current_user, role)   # a Super Admin removed someone else's review
        announce = dict(
            review_id=review.id, item_id=item.id, item_name=_item_name(kind, item),
            tenant_id=next((t for t in _owner_tenants(item) if t), None), previous_status=review.moderation_status,
            actor=current_user, actor_role=role, access_token=access_token,
        )
    db.delete(review)
    db.commit()
    if announce:
        review_notifications.notify_review_deleted(kind, **announce)
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
    if not check_move(previous, action):
        return review  # already there: nothing to record or announce
    _log(db, kind, item, review, action, actor, access.role)
    review.moderation_status = action
    db.commit()
    db.refresh(review)
    review_notifications.notify_review_decision(kind, review.id, action, access_token=access_token)
    review_notifications.notify_review_moderated(
        kind, review_id=review.id, item_id=item.id, item_name=_item_name(kind, item),
        tenant_id=next((t for t in _owner_tenants(item) if t), None), previous_status=previous, new_status=action,
        actor=actor, actor_role=access.role, access_token=access_token,
    )
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
