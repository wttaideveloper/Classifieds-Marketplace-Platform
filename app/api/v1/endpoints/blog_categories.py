from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_admin
from app.db.database import get_db
from app.schemas.blog_schema import BlogCategoryCreateSchema, BlogCategoryUpdateSchema
from app.services.blog_service import (
    create_blog_category_service,
    update_blog_category_service,
    delete_blog_category_service,
    list_blog_categories_service,
)


router = APIRouter(tags=["Blog Categories"])


@router.post("/admin/blog-categories", status_code=status.HTTP_201_CREATED)
def create_category(
    payload: BlogCategoryCreateSchema,
    current_admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    return create_blog_category_service(db=db, payload=payload)


@router.put("/admin/blog-categories/{category_id}", status_code=status.HTTP_200_OK)
def update_category(
    category_id: str,
    payload: BlogCategoryUpdateSchema,
    current_admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    return update_blog_category_service(db=db, category_id=category_id, payload=payload)


@router.delete("/admin/blog-categories/{category_id}", status_code=status.HTTP_200_OK)
def delete_category(
    category_id: str,
    current_admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    return delete_blog_category_service(db=db, category_id=category_id)


@router.get("/blog-categories", status_code=status.HTTP_200_OK)
def list_categories(
    db: Session = Depends(get_db),
):
    return list_blog_categories_service(db=db)

