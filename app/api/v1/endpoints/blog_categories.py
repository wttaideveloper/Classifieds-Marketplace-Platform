from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.schemas.blog_schema import BlogCategoryCreateSchema, BlogCategoryUpdateSchema
from app.services.blog_service import (
    create_blog_category_service,
    update_blog_category_service,
    delete_blog_category_service,
    list_blog_categories_service,
)


router = APIRouter(tags=["Blog Categories"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/admin/blog-categories", status_code=status.HTTP_201_CREATED)
def create_category(
    payload: BlogCategoryCreateSchema,
    db: Session = Depends(get_db),
):
    return create_blog_category_service(db=db, payload=payload)


@router.put("/admin/blog-categories/{categoryId}", status_code=status.HTTP_200_OK)
def update_category(
    categoryId: str,
    payload: BlogCategoryUpdateSchema,
    db: Session = Depends(get_db),
):
    return update_blog_category_service(db=db, category_id=categoryId, payload=payload)


@router.delete("/admin/blog-categories/{categoryId}", status_code=status.HTTP_200_OK)
def delete_category(
    categoryId: str,
    db: Session = Depends(get_db),
):
    return delete_blog_category_service(db=db, category_id=categoryId)


@router.get("/blog-categories", status_code=status.HTTP_200_OK)
def list_categories(
    db: Session = Depends(get_db),
):
    return list_blog_categories_service(db=db)

