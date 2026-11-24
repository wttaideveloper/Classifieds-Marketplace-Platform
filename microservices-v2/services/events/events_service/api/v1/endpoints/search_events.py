"""GET /api/v1/search/events (moved from the catalog's search router: events own the data). Copied from
app/api/v1/endpoints/search.py: search_events."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from events_service.db.database import get_db
from events_service.repository.event_repo import build_event_viewer
from events_service.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from events_service.schemas.event_schema import EventPaginatedResponse
from events_service.services.search_events_service import search_events_service
from mskit.auth.dependencies import extract_access_token, get_optional_current_user

router = APIRouter(tags=["Search"])


@router.get(
    "/events",
    response_model=EventPaginatedResponse,
    summary="Search Events",
)
def search_events(
    request: Request,
    query: str | None = Query(None, description="Search query."),
    tenant_id: UUID | None = Query(None),
    enterprise_id: UUID | None = Query(None),
    category: str | None = Query(None),
    city: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    delivery_mode: str | None = Query(None),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    current_user: dict | None = Depends(get_optional_current_user),
):
    # Same visibility rule as GET /events: published for the public, plus the caller's own tenant for staff.
    viewer = build_event_viewer(
        db,
        current_user,
        access_token=extract_access_token(request),
        remote_platform_check=bool(status_filter) and status_filter != "published",
    )
    return search_events_service(
        db, query=query, tenant_id=tenant_id, enterprise_id=enterprise_id, category=category, city=city, status_filter=status_filter, delivery_mode=delivery_mode, page=page, page_size=page_size, viewer=viewer,
    )

