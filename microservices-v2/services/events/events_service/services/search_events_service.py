"""GET /api/v1/search/events, served by the events service (it owns the data). Copied from
app/services/search_service.py: search_events_service."""
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from events_service.models.event_model import Event
from events_service.models.location_model import EnterpriseLocation  # read-only replica
from events_service.repository.query_utils import apply_ilike_search, apply_soft_delete_filter, build_pagination_meta, paginate_query
from events_service.schemas.event_schema import EventListItemResponse, EventPaginatedResponse
from events_service.services.response_mappers import map_event_list_item


def search_events_service(
    db: Session,
    *,
    query: str | None = None,
    tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None,
    category: str | None = None,
    city: str | None = None,
    status_filter: str | None = None,
    delivery_mode: str | None = None,
    page: int = 1,
    page_size: int = 20,
    viewer=None,
) -> EventPaginatedResponse:
    from events_service.repository.event_repo import EventViewer, event_visibility_filter
    from events_service.services.event_service import _redact_event_data, _registered_event_ids

    viewer = viewer or EventViewer()  # no viewer == anonymous public reader (published only)
    db_query = db.query(Event).options(joinedload(Event.enterprise))
    db_query = apply_soft_delete_filter(db_query, Event, False)
    visibility = event_visibility_filter(viewer)
    if visibility is not None:
        db_query = db_query.filter(visibility)

    if tenant_id:
        db_query = db_query.filter(Event.tenant_id == tenant_id)
    if enterprise_id:
        db_query = db_query.filter(Event.enterprise_id == enterprise_id)
    if category:
        db_query = db_query.filter(Event.category == category)
    if status_filter:
        db_query = db_query.filter(Event.status == status_filter)
    if delivery_mode:
        db_query = db_query.filter(Event.delivery_mode == delivery_mode)
    if city:
        db_query = db_query.outerjoin(
            EnterpriseLocation,
            Event.location_id == EnterpriseLocation.id,
        ).filter(EnterpriseLocation.city.ilike(f"%{city.strip()}%"))
    if query:
        db_query = apply_ilike_search(
            db_query,
            [Event.title, Event.description, Event.category, Event.subcategory],
            query,
        )

    db_query = db_query.order_by(Event.created_at.desc())
    items, total = paginate_query(db_query, page, page_size)
    registered = _registered_event_ids(db, (viewer.user or {}).get("email"), [e.id for e in items])   # one query for the page
    return EventPaginatedResponse(
        items=[
            EventListItemResponse.model_validate(
                {**_redact_event_data(map_event_list_item(item), item, viewer), "registration_status": item.id in registered}
            )
            for item in items
        ],
        pagination=build_pagination_meta(total, page, page_size),
    )

