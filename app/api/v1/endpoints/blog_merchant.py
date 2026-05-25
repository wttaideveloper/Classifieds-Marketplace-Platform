from fastapi import APIRouter, Depends, status, Query
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.schemas.blog_schema import BlogCreateSchema, BlogUpdateSchema
from app.services.blog_service import (
    create_blog_service,
    update_blog_service,
    delete_blog_service,
    get_merchant_blogs_service,
    submit_blog_for_approval_service,
)


router = APIRouter(tags=["Merchant Blogs"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/blogs", status_code=status.HTTP_201_CREATED)
def create_blog(
    payload: BlogCreateSchema,
    merchantId: str = Query(..., description="Merchant id"),
    db: Session = Depends(get_db),
):
    return create_blog_service(db=db, merchant_id=merchantId, payload=payload)


@router.put("/blogs/{blogId}", status_code=status.HTTP_200_OK)
def update_blog(
    blogId: str,
    payload: BlogUpdateSchema,
    merchantId: str = Query(..., description="Merchant id"),
    db: Session = Depends(get_db),
):
    return update_blog_service(
        db=db,
        merchant_id=merchantId,
        blog_id=blogId,
        payload=payload,
    )


@router.delete("/blogs/{blogId}", status_code=status.HTTP_200_OK)
def delete_blog(
    blogId: str,
    merchantId: str = Query(..., description="Merchant id"),
    db: Session = Depends(get_db),
):
    return delete_blog_service(db=db, merchant_id=merchantId, blog_id=blogId)


@router.get("/blogs", status_code=status.HTTP_200_OK)
def get_merchant_blogs(
    merchantId: str = Query(..., description="Merchant id"),
    search: str = None,
    status_filter: str = Query(default=None, alias="status"),
    page: int = 1,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    return get_merchant_blogs_service(
        db=db,
        merchant_id=merchantId,
        search=search,
        status_filter=status_filter,
        page=page,
        limit=limit,
    )


@router.put("/blogs/{blogId}/submit", status_code=status.HTTP_200_OK)
def submit_blog(
    blogId: str,
    merchantId: str = Query(..., description="Merchant id"),
    db: Session = Depends(get_db),
):
    return submit_blog_for_approval_service(db=db, merchant_id=merchantId, blog_id=blogId)

