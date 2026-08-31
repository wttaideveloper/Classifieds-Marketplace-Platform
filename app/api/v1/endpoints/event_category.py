from uuid import UUID

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_admin
from app.db.database import get_db
from app.schemas.event_schema import (
    EventCategoryCreate,
    EventCategoryResponse,
    EventCategoryUpdate,
)
from app.services.event_service import (
    create_event_category_service,
    delete_event_category_service,
    list_event_categories_service,
    update_event_category_service,
)

router = APIRouter(tags=["Event Categories"])


@router.post(
    "/",
    response_model=EventCategoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Event Category",
    description="Admin-only. Create a top-level category or a subcategory under an existing parent.",
)
def create_category(
    payload: EventCategoryCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_admin),
):
    return create_event_category_service(db, payload)


@router.get(
    "/",
    response_model=list[EventCategoryResponse],
    summary="List Event Categories",
    description="Returns all categories (flat list with parent_id for hierarchy).",
)
def list_categories(db: Session = Depends(get_db)):
    return list_event_categories_service(db)


@router.put(
    "/{category_id}",
    response_model=EventCategoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Event Category",
    description="Admin-only. Rename a category or update its description.",
)
def update_category(
    category_id: UUID,
    payload: EventCategoryUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_admin),
):
    return update_event_category_service(db, category_id, payload)


@router.delete(
    "/{category_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete Event Category",
    description="Admin-only. Fails if the category has subcategories.",
)
def delete_category(
    category_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_admin),
):
    return delete_event_category_service(db, category_id)
