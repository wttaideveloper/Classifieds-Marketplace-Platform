from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.models.event_model import Event
from app.repository.query_utils import (
    apply_ilike_search,
    apply_soft_delete_filter,
    paginate_query,
)


def create_event(db: Session, event_data):
    payload = event_data.to_model_data() if hasattr(event_data, "to_model_data") else event_data.model_dump()
    event = Event(**payload)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def get_events(
    db: Session,
    *,
    search: str | None = None,
    category: str | None = None,
    tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None,
    location_id: UUID | None = None,
    status: str | None = None,
    delivery_mode: str | None = None,
    page: int = 1,
    page_size: int = 20,
    include_deleted: bool = False,
):
    query = db.query(Event).options(joinedload(Event.enterprise))
    query = apply_soft_delete_filter(query, Event, include_deleted)

    if tenant_id:
        query = query.filter(Event.tenant_id == tenant_id)
    if enterprise_id:
        query = query.filter(Event.enterprise_id == enterprise_id)
    if location_id:
        query = query.filter(Event.location_id == location_id)
    if category:
        query = query.filter(Event.category == category)
    if status:
        query = query.filter(Event.status == status)
    if delivery_mode:
        query = query.filter(Event.delivery_mode == delivery_mode)
    if search:
        query = apply_ilike_search(
            query,
            [Event.title, Event.description, Event.category, Event.subcategory],
            search,
        )

    query = query.order_by(Event.created_at.desc())
    return paginate_query(query, page, page_size)


def get_event_by_id(db: Session, event_id: UUID, include_deleted: bool = False):
    query = db.query(Event).options(joinedload(Event.enterprise)).filter(Event.id == event_id)
    if not include_deleted:
        query = apply_soft_delete_filter(query, Event, include_deleted)
    return query.first()


def update_event(db: Session, event, update_data):
    payload = update_data.to_model_data() if hasattr(update_data, "to_model_data") else update_data.model_dump(exclude_unset=True)
    for key, value in payload.items():
        setattr(event, key, value)
    db.commit()
    db.refresh(event)
    return event


def delete_event(db: Session, event):
    event.is_deleted = True
    event.status = "inactive"
    db.commit()
    db.refresh(event)
    return event
