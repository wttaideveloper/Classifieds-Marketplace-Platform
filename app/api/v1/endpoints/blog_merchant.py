from fastapi import APIRouter, Depends, status, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.schemas.blog_schema import BlogCreateSchema, BlogUpdateSchema
from app.services.blog_service import (
    create_blog_service,
    update_blog_service,
    delete_blog_service,
    get_merchant_blogs_service,
    submit_blog_for_approval_service,
)


router = APIRouter(tags=["Merchant Blogs"], dependencies=[Depends(get_current_user)])


@router.post("/blogs", status_code=status.HTTP_201_CREATED)
def create_blog(
    payload: BlogCreateSchema,
    merchant_id: str = Query(..., description="Merchant id"),
    db: Session = Depends(get_db),
):
    return create_blog_service(db=db, merchant_id=merchant_id, payload=payload)


@router.put("/blogs/{blog_id}", status_code=status.HTTP_200_OK)
def update_blog(
    blog_id: str,
    payload: BlogUpdateSchema,
    merchant_id: str = Query(..., description="Merchant id"),
    db: Session = Depends(get_db),
):
    return update_blog_service(
        db=db,
        merchant_id=merchant_id,
        blog_id=blog_id,
        payload=payload,
    )


@router.delete("/blogs/{blog_id}", status_code=status.HTTP_200_OK)
def delete_blog(
    blog_id: str,
    merchant_id: str = Query(..., description="Merchant id"),
    db: Session = Depends(get_db),
):
    return delete_blog_service(db=db, merchant_id=merchant_id, blog_id=blog_id)


@router.get("/blogs", status_code=status.HTTP_200_OK)
def get_merchant_blogs(
    merchant_id: str = Query(..., description="Merchant id"),
    search: str = None,
    status_filter: str = Query(default=None, alias="status"),
    page: int = 1,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    return get_merchant_blogs_service(
        db=db,
        merchant_id=merchant_id,
        search=search,
        status_filter=status_filter,
        page=page,
        limit=limit,
    )


@router.put("/blogs/{blog_id}/submit", status_code=status.HTTP_200_OK)
def submit_blog(
    blog_id: str,
    merchant_id: str = Query(..., description="Merchant id"),
    db: Session = Depends(get_db),
):
    return submit_blog_for_approval_service(db=db, merchant_id=merchant_id, blog_id=blog_id)

