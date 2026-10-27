"""Event wishlist: save / unsave an event, and list the ones the caller saved (same pattern as the training wishlist).

Saving is idempotent. A saved event is listed only while the caller can still see it (published, or one they hold a
registration for), so an event that was unpublished or deleted quietly drops out of the list and comes back if it returns.
"""
from datetime import timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.event_aux_models import EventWishlistItem
from app.repository.event_repo import build_event_viewer, can_view_event, get_event_by_id
from app.utils.event_utils import get_event_timezone, resolve_naive_or_aware


def _user_uuid(current_user: dict) -> UUID:
    try:
        return UUID(str(current_user.get("id")))
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


def _utc_text(dt, event) -> str | None:
    """The event's own wall-clock time as a UTC instant, written with a Z. `time_zone` is returned next to it."""
    if dt is None:
        return None
    aware = resolve_naive_or_aware(dt, get_event_timezone(event))
    return aware.astimezone(timezone.utc).replace(tzinfo=None).isoformat() + "Z"


def _location(event) -> str | None:
    venue = event.venue if isinstance(event.venue, dict) else {}
    if venue.get("city"):
        return venue["city"]
    if venue.get("name"):
        return venue["name"]
    place = getattr(event, "location", None)
    if place is not None:
        return place.city or place.location_name
    return None


def _item(item: EventWishlistItem, event, stats: dict) -> dict:
    average, count = stats.get(event.id, (None, 0))
    enterprise = getattr(event, "enterprise", None)
    return {
        "id": str(item.id), "event_id": str(event.id), "title": event.title, "primary_image": event.primary_image,
        "price": event.price, "currency": event.currency, "pricing_type": event.pricing_type,
        "start_at": _utc_text(event.start_date, event), "end_at": _utc_text(event.end_date, event), "time_zone": event.time_zone,
        "delivery_mode": event.delivery_mode, "enterprise_name": enterprise.business_short_name if enterprise is not None else None,
        "location": _location(event), "average_rating": average, "reviews_count": count,
        "added_at": item.created_at.isoformat() + "Z", "status": event.status,
    }


def _viewer(db: Session, current_user: dict, access_token: str | None):
    return build_event_viewer(db, current_user, access_token=access_token)


def add_wishlist(db: Session, current_user: dict, event_id: UUID, access_token: str | None = None) -> tuple[dict, bool]:
    """Returns (item, created). An event the caller may not see is a plain 404, like the event detail."""
    from app.services.event_service import _approved_review_stats

    user_id = _user_uuid(current_user)
    event = get_event_by_id(db, event_id)
    if event is None or not can_view_event(db, event, _viewer(db, current_user, access_token), access_token=access_token):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    item = db.query(EventWishlistItem).filter(EventWishlistItem.user_id == user_id, EventWishlistItem.event_id == event_id).first()
    created = item is None
    if created:
        item = EventWishlistItem(user_id=user_id, event_id=event_id)
        db.add(item)
        try:
            db.commit()
        except Exception:
            db.rollback()             # two taps at once: the other request saved it first
            item = db.query(EventWishlistItem).filter(EventWishlistItem.user_id == user_id, EventWishlistItem.event_id == event_id).first()
            if item is None:
                raise
            created = False
        else:
            db.refresh(item)
    return _item(item, event, _approved_review_stats(db, [event_id])), created


def remove_wishlist(db: Session, current_user: dict, event_id: UUID) -> None:
    """Idempotent: removing an event that is not saved is not an error."""
    db.query(EventWishlistItem).filter(EventWishlistItem.user_id == _user_uuid(current_user), EventWishlistItem.event_id == event_id).delete(
        synchronize_session=False)
    db.commit()


def list_wishlist(db: Session, current_user: dict, access_token: str | None = None) -> list[dict]:
    from app.services.event_service import _approved_review_stats

    user_id = _user_uuid(current_user)
    rows = db.query(EventWishlistItem).filter(EventWishlistItem.user_id == user_id).order_by(EventWishlistItem.created_at.desc()).all()
    if not rows:
        return []
    viewer = _viewer(db, current_user, access_token)
    pairs = []
    for row in rows:
        event = get_event_by_id(db, row.event_id)             # None for a deleted event
        if event is not None and can_view_event(db, event, viewer, access_token=access_token):
            pairs.append((row, event))
    stats = _approved_review_stats(db, [event.id for _, event in pairs])
    return [_item(row, event, stats) for row, event in pairs]


def wishlisted_ids(db: Session, user: dict | None, event_ids: list) -> set:
    """Ids (of `event_ids`) the caller has saved, in one query. Empty for an anonymous caller."""
    if not user or not event_ids:
        return set()
    try:
        user_id = UUID(str(user.get("id")))
    except (ValueError, TypeError):
        return set()
    return {r[0] for r in db.query(EventWishlistItem.event_id).filter(EventWishlistItem.user_id == user_id, EventWishlistItem.event_id.in_(event_ids)).all()}
