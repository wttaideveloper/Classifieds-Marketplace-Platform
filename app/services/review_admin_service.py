"""The moderation queue across modules (training/course, product, service, event).

    list_reviews_for_moderation   every review the caller may moderate, any status, newest first, with counts per status
    moderate_any_review           approve / reject / reset a review given only its module and id

Scope: an Enterprise Admin (and a provider, read-only) sees the reviews of items their own business owns;
an active Super Admin sees every business. The same ownership rules as the per-module moderate endpoints apply.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.repository.query_utils import build_pagination_meta
from app.services.review_access import assert_can_moderate, can_view_all_tenants
from app.services.review_common import MODERATION_STATUSES, parse_rating, public_name, validate_action

_EVERYONE = type("Everyone", (), {"role": "super_admin", "tenant_id": None})()

MODULES = ("training", "product", "service", "event")
_ALIASES = {"course": "training", "courses": "training", "trainings": "training", "products": "product",
            "services": "service", "events": "event"}


def normalize_module(module: str | None) -> str | None:
    if module is None or not str(module).strip():
        return None
    key = str(module).strip().lower()
    key = _ALIASES.get(key, key)
    if key not in MODULES:
        raise HTTPException(status_code=422, detail=f"module must be one of: {', '.join(MODULES)} (course = training)")
    return key


def _assert_staff(access) -> None:
    if access is None or access.role == "public":
        raise HTTPException(status_code=401, detail="Not authenticated")
    if access.role not in ("admin", "provider", "super_admin"):
        raise HTTPException(status_code=403, detail="Not authorized")


def _scope_for_business(access, tenant_id):
    """The access to scope a query with. A Super Admin who names a business sees that business only (as its
    Enterprise Admin would); anyone else may only name their own business."""
    if tenant_id is None:
        return access
    if access.role == "super_admin":
        return SimpleNamespace(role="admin", tenant_id=tenant_id, provider_user_id=None)
    if str(access.tenant_id).lower() != str(tenant_id).lower():
        raise HTTPException(status_code=403, detail="Not authorized for this tenant")
    return access


# ----------------------------------------------------------------------------- per-module queries

def _scoped_query(db: Session, module: str, access):
    """(query of (review, item), review model, item model, name column, item fk column, item display name column)."""
    t = access.tenant_id
    everyone = can_view_all_tenants(access)
    if module == "training":
        from app.models.enterprise_model import Enterprise
        from app.models.training_model import Training, TrainingReview

        q = db.query(TrainingReview, Training).join(Training, Training.id == TrainingReview.training_id)
        q = q.options(joinedload(Training.enterprise)).filter(Training.is_deleted.is_(False))
        if not everyone:
            q = q.outerjoin(Enterprise, Training.enterprise_id == Enterprise.id).filter(sa.or_(
                sa.and_(Enterprise.tenant_id == t, sa.or_(Training.tenant_id.is_(None), Training.tenant_id == t)),
                sa.and_(Enterprise.id.is_(None), Training.tenant_id == t),
            ))
        return q, TrainingReview, Training, TrainingReview.training_id, Training.title
    if module in ("product", "service"):
        from app.core.catalog_access import scope_catalog_query

        if module == "product":
            from app.models.product_model import Product as Item, ProductReview as Review

            fk, name = Review.product_id, Item.product_name
        else:
            from app.models.service_model import Service as Item, ServiceReview as Review

            fk, name = Review.service_id, Item.service_name
        q = db.query(Review, Item).join(Item, Item.id == fk).options(joinedload(Item.enterprise))
        if hasattr(Item, "is_deleted"):
            q = q.filter(Item.is_deleted.is_(False))
        if not everyone:
            q = scope_catalog_query(q, Item, access)
        return q, Review, Item, fk, name
    from app.models.event_aux_models import EventFeedback
    from app.models.event_model import Event
    from app.repository.event_repo import event_tenant_clause

    q = db.query(EventFeedback, Event).join(Event, Event.id == EventFeedback.event_id)
    q = q.options(joinedload(Event.enterprise)).filter(EventFeedback.is_review.is_(True))
    if hasattr(Event, "is_deleted"):
        q = q.filter(Event.is_deleted.is_(False))
    if not everyone:
        clause = event_tenant_clause(t)
        q = q.filter(clause) if clause is not None else q.filter(sa.false())
    return q, EventFeedback, Event, EventFeedback.event_id, Event.title


def _names(db: Session, module: str, rows: list) -> dict:
    """Display names for the reviewers in `rows` (review, item) pairs; an email is never used as a name."""
    if module == "training":
        from app.models.training_model import TrainingEnrolment

        found: dict = {}
        for review, _ in rows:
            for e in db.query(TrainingEnrolment).filter(
                TrainingEnrolment.training_id == review.training_id,
                TrainingEnrolment.participant_email == review.participant_email,
            ).order_by(TrainingEnrolment.created_at.desc()).all():
                name = public_name(e.participant_name)
                if name:
                    found[(review.training_id, review.participant_email)] = name
                    break
        return found
    if module == "event":
        from app.models.event_aux_models import EventRegistration

        found = {}
        for review, _ in rows:
            email = (review.participant_email or "").strip().lower()
            if not email:
                continue
            reg = db.query(EventRegistration).filter(
                EventRegistration.event_id == review.event_id,
                sa.func.lower(EventRegistration.participant_email) == email,
            ).order_by(EventRegistration.created_at.asc()).first()
            name = public_name(reg.participant_name) if reg else None
            if name:
                found[(review.event_id, email)] = name
        return found
    return {}


def _row(module: str, review, item, name_column_value, names: dict) -> dict:
    enterprise = getattr(item, "enterprise", None)
    base = {
        "module": module,
        "review_id": review.id,
        "item_id": item.id,
        "item_name": name_column_value,
        "rating": parse_rating(review.rating),
        "comment": review.comment,
        "moderation_status": review.moderation_status or "pending",
        "created_at": review.created_at,
        "updated_at": getattr(review, "updated_at", None) or review.created_at,
        "tenant_id": (enterprise.tenant_id if enterprise is not None else None) or getattr(item, "tenant_id", None),
        "business_name": getattr(enterprise, "business_short_name", None) if enterprise is not None else None,
    }
    if module == "training":
        base.update(
            reviewer_name=names.get((review.training_id, review.participant_email)),
            reviewer_email=review.participant_email, reviewer_user_id=None, is_verified=True,
        )
    elif module == "event":
        base.update(
            reviewer_name=names.get((review.event_id, (review.participant_email or "").strip().lower())),
            reviewer_email=review.participant_email, reviewer_user_id=review.user_id, is_verified=True,
        )
    else:
        base.update(
            reviewer_name=public_name(review.reviewer_name), reviewer_email=None,
            reviewer_user_id=review.user_id, is_verified=bool(review.is_verified_purchase),
        )
    return base


def list_reviews_for_moderation(
    db: Session,
    access,
    *,
    module: str | None = None,
    status: str | None = None,
    item_id: UUID | None = None,
    rating: int | None = None,
    q: str | None = None,
    tenant_id: UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    _assert_staff(access)
    wanted_module = normalize_module(module)
    scope = _scope_for_business(access, tenant_id)
    if status is not None and status not in MODERATION_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of: {', '.join(MODERATION_STATUSES)}")
    if rating is not None and not 1 <= rating <= 5:
        raise HTTPException(status_code=422, detail="rating must be a whole number from 1 to 5")

    counts = {s: 0 for s in MODERATION_STATUSES}
    total = 0
    collected: list[dict] = []
    for current in ([wanted_module] if wanted_module else list(MODULES)):
        query, Review, Item, fk, name_col = _scoped_query(db, current, scope)
        if item_id is not None:
            query = query.filter(fk == item_id)
        if rating is not None:
            query = query.filter(Review.rating == str(rating))
        if q and q.strip():
            query = query.filter(Review.comment.ilike(f"%{q.strip()}%"))

        for review_status, n in query.with_entities(Review.moderation_status, sa.func.count(Review.id)).group_by(
            Review.moderation_status
        ).all():
            counts[review_status if review_status in counts else "pending"] += n

        listed = query
        if status is not None:
            listed = listed.filter(
                Review.moderation_status == status if status != "pending"
                else sa.or_(Review.moderation_status == "pending", Review.moderation_status.is_(None))
            )
        total += listed.order_by(None).with_entities(sa.func.count(Review.id)).scalar() or 0
        pairs = listed.order_by(Review.created_at.desc(), Review.id).limit(page * page_size).all()
        names = _names(db, current, pairs)
        for review, item in pairs:
            collected.append(_row(current, review, item, getattr(item, name_col.key, None), names))

    collected.sort(key=lambda r: (r["created_at"], str(r["review_id"])), reverse=True)
    start = (page - 1) * page_size
    return {
        "items": collected[start:start + page_size],
        "counts": counts,
        "pagination": build_pagination_meta(total, page, page_size),
    }


# ----------------------------------------------------------------------------- delete by module + id

def delete_any_review(db: Session, module: str, review_id: UUID, current_user: dict, *, access_token: str | None = None) -> dict:
    """Delete one review given only its module and id. Same rules as the module's own delete: the author, or staff of
    the business that owns the item, or a Super Admin; deleting someone else's review is written to the history."""
    current = normalize_module(module)
    if current is None:
        raise HTTPException(status_code=422, detail=f"module must be one of: {', '.join(MODULES)} (course = training)")

    if current == "training":
        from app.models.training_model import TrainingReview
        from app.services.training_service import delete_training_review_service

        review = db.get(TrainingReview, review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="Review not found")
        return delete_training_review_service(db, review.training_id, review_id, current_user, access_token=access_token)
    if current == "product":
        from app.models.product_model import ProductReview
        from app.services.product_service import delete_product_review_service

        review = db.get(ProductReview, review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="Review not found")
        return delete_product_review_service(db, review.product_id, review_id, current_user, access_token=access_token)
    if current == "service":
        from app.models.service_model import ServiceReview
        from app.services.service_service import delete_service_review_service

        review = db.get(ServiceReview, review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="Review not found")
        return delete_service_review_service(db, review.service_id, review_id, current_user, access_token=access_token)
    from app.models.event_aux_models import EventFeedback
    from app.services.event_service import delete_event_review_service

    review = db.get(EventFeedback, review_id)
    if review is None or not review.is_review:
        raise HTTPException(status_code=404, detail="Review not found")
    return delete_event_review_service(db, review.event_id, review_id, current_user, access_token=access_token)


# ----------------------------------------------------------------------------- moderate by module + id

def moderate_any_review(db: Session, module: str, review_id: UUID, action: str, access, *, access_token: str | None = None, actor: dict | None = None) -> dict:
    """Approve / reject / reset one review. Returns the same row shape as the list."""
    validate_action(action)
    current = normalize_module(module)
    if current is None:
        raise HTTPException(status_code=422, detail=f"module must be one of: {', '.join(MODULES)} (course = training)")
    if access is None or access.role == "public":
        raise HTTPException(status_code=401, detail="Not authenticated")

    if current == "training":
        from app.models.training_model import TrainingReview
        from app.services.training_service import moderate_training_review_service

        review = db.get(TrainingReview, review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="Review not found")
        moderate_training_review_service(db, review.training_id, review_id, action, access, access_token=access_token, actor=actor)
    elif current == "product":
        from app.models.product_model import ProductReview
        from app.services.product_service import moderate_product_review_service

        review = db.get(ProductReview, review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="Review not found")
        moderate_product_review_service(db, review.product_id, review_id, action, access, access_token=access_token, actor=actor)
    elif current == "service":
        from app.models.service_model import ServiceReview
        from app.services.service_service import moderate_service_review_service

        review = db.get(ServiceReview, review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="Review not found")
        moderate_service_review_service(db, review.service_id, review_id, action, access, access_token=access_token, actor=actor)
    else:
        from app.models.event_aux_models import EventFeedback
        from app.repository.event_repo import event_owner_tenant_id, get_event_by_id
        from app.services.event_service import moderate_review_service

        review = db.get(EventFeedback, review_id)
        if review is None or not review.is_review:
            raise HTTPException(status_code=404, detail="Review not found")
        event = get_event_by_id(db, review.event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Review not found")
        assert_can_moderate(access, event_owner_tenant_id(event))
        moderate_review_service(db, review_id, action, event_id=review.event_id, access_token=access_token, actor=actor)

    # Re-read it through the list query so the answer has the same shape (and is scoped the same way).
    # The change is already saved, so look the row up without the caller's scope if the scoped lookup misses it.
    for scope in (access, _EVERYONE):
        query, Review, Item, fk, name_col = _scoped_query(db, current, scope)
        pair = query.filter(Review.id == review_id).first()
        if pair is not None:
            break
    else:
        raise HTTPException(status_code=404, detail="Review not found")
    review, item = pair
    return _row(current, review, item, getattr(item, name_col.key, None), _names(db, current, [pair]))
