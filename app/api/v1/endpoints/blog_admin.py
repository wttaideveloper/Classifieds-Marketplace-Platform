from fastapi import APIRouter, Depends, status, Query
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.schemas.blog_schema import BlogRejectSchema
from app.services.blog_service import (
    get_pending_blogs_service,
    approve_blog_service,
    reject_blog_service,
    admin_list_blogs_service,
)


router = APIRouter(tags=["Admin Blogs"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/blogs/pending", status_code=status.HTTP_200_OK)
def get_pending_blogs(
    search: str = None,
    page: int = 1,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    return get_pending_blogs_service(db=db, page=page, limit=limit, search=search)


@router.put("/blogs/{blogId}/approve", status_code=status.HTTP_200_OK)
def approve_blog(
    blogId: str,
    adminId: str = Query(..., description="Admin id"),
    db: Session = Depends(get_db),
):
    return approve_blog_service(db=db, admin_id=adminId, blog_id=blogId)


@router.put("/blogs/{blogId}/reject", status_code=status.HTTP_200_OK)
def reject_blog(
    blogId: str,
    payload: BlogRejectSchema,
    adminId: str = Query(..., description="Admin id"),
    db: Session = Depends(get_db),
):
    return reject_blog_service(db=db, admin_id=adminId, blog_id=blogId, remarks=payload.remarks)


@router.get("/blogs", status_code=status.HTTP_200_OK)
def admin_list_blogs(
    merchantId: str = None,
    status_filter: str = Query(default=None, alias="status"),
    approval_filter: str = Query(default=None, alias="approvalStatus"),
    search: str = None,
    page: int = 1,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    return admin_list_blogs_service(
        db=db,
        merchant_id=merchantId,
        status_filter=status_filter,
        approval_filter=approval_filter,
        search=search,
        page=page,
        limit=limit,
    )

