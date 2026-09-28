from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.dependencies import require_form_configuration_super_admin
from app.db.database import get_db
from app.schemas.training_schema import (
    TrainingCategoryCreate,
    TrainingCategoryResponse,
    TrainingCategoryUpdate,
)
from app.services.training_service import (
    create_training_category_service,
    delete_training_category_service,
    list_training_categories_service,
    update_training_category_service,
)

router = APIRouter(tags=["Training Categories"])


@router.post(
    "/",
    response_model=TrainingCategoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Training Category",
    description="Super Admin-only. Create a top-level category or a subcategory under an existing parent. "
                "Own taxonomy — separate from Event categories.",
)
def create_category(
    payload: TrainingCategoryCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_form_configuration_super_admin),
):
    return create_training_category_service(db, payload)


@router.get(
    "/",
    response_model=list[TrainingCategoryResponse],
    summary="List Training Categories",
    description="Public. Returns all categories (flat list with parent_id for hierarchy).",
)
def list_categories(db: Session = Depends(get_db)):
    return list_training_categories_service(db)


@router.put(
    "/{category_id}",
    response_model=TrainingCategoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Training Category",
    description="Super Admin-only. Rename a category or update its description. parent_id cannot be changed "
                "after creation — delete and recreate under the new parent instead.",
)
def update_category(
    category_id: UUID,
    payload: TrainingCategoryUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_form_configuration_super_admin),
):
    return update_training_category_service(db, category_id, payload)


@router.delete(
    "/{category_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete Training Category",
    description="Super Admin-only. Fails if the category has subcategories. Existing Trainings referencing this "
                "category/subcategory by name are not affected — Training.category/subcategory remain free-text "
                "columns; deleting a taxonomy entry does not touch existing records.",
)
def delete_category(
    category_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_form_configuration_super_admin),
):
    return delete_training_category_service(db, category_id)
