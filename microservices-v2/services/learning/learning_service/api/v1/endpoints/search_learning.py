"""GET /api/v1/search/trainings and /search/programs (moved from the catalog's search router: learning owns the data).
Copied from app/api/v1/endpoints/search.py."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from learning_service.db.database import get_db
from learning_service.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from learning_service.schemas.program_schema import ProgramPaginatedResponse
from learning_service.schemas.training_schema import TrainingPaginatedResponse
from learning_service.services.search_learning_service import search_programs_service, search_trainings_service
from mskit.auth.dependencies import extract_access_token, get_optional_current_user

router = APIRouter(tags=["Search"])


@router.get(
    "/trainings",
    response_model=TrainingPaginatedResponse,
    summary="Search Trainings",
    description="query matches (case-insensitive, substring) against title, description, category, and tags. "
                "Other params filter exactly: tenant_id, enterprise_id, category, city (via location), status.",
)
def search_trainings(
    query: str | None = Query(None, description="Case-insensitive substring match against title, description, category, and tags."),
    tenant_id: UUID | None = Query(None),
    enterprise_id: UUID | None = Query(None),
    category: str | None = Query(None),
    city: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
):
    return search_trainings_service(
        db, query=query, tenant_id=tenant_id, enterprise_id=enterprise_id, category=category, city=city, status_filter=status_filter, page=page, page_size=page_size,
    )


@router.get(
    "/programs",
    response_model=ProgramPaginatedResponse,
    summary="Search Programs",
)
def search_programs(
    query: str | None = Query(None),
    tenant_id: UUID | None = Query(None),
    enterprise_id: UUID | None = Query(None),
    category: str | None = Query(None),
    city: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
):
    return search_programs_service(
        db, query=query, tenant_id=tenant_id, enterprise_id=enterprise_id, category=category, city=city, status_filter=status_filter, page=page, page_size=page_size,
    )

