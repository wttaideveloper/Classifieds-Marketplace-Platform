from fastapi import APIRouter, Depends, status, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_admin
from app.db.database import get_db
from app.schemas.blog_schema import BlogRejectSchema
from app.services.blog_service import (
    get_pending_blogs_service,
    approve_blog_service,
    reject_blog_service,
    admin_list_blogs_service,
)


router = APIRouter(tags=["Admin Blogs"], dependencies=[Depends(get_current_admin)])


@router.get("/blogs/pending", status_code=status.HTTP_200_OK)
def get_pending_blogs(
    search: str = None,
    page: int = 1,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    return get_pending_blogs_service(db=db, page=page, limit=limit, search=search)


@router.put("/blogs/{blog_id}/approve", status_code=status.HTTP_200_OK)
def approve_blog(
    blog_id: str,
    admin_id: str = Query(..., description="Admin id"),
    db: Session = Depends(get_db),
):
    return approve_blog_service(db=db, admin_id=admin_id, blog_id=blog_id)


@router.put("/blogs/{blog_id}/reject", status_code=status.HTTP_200_OK)
def reject_blog(
    blog_id: str,
    payload: BlogRejectSchema,
    admin_id: str = Query(..., description="Admin id"),
    db: Session = Depends(get_db),
):
    return reject_blog_service(db=db, admin_id=admin_id, blog_id=blog_id, remarks=payload.remarks)


@router.get("/blogs", status_code=status.HTTP_200_OK)
def admin_list_blogs(
    merchant_id: str = None,
    status_filter: str = Query(default=None, alias="status"),
    approval_filter: str = Query(default=None, alias="approval_status"),
    search: str = None,
    page: int = 1,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    return admin_list_blogs_service(
        db=db,
        merchant_id=merchant_id,
        status_filter=status_filter,
        approval_filter=approval_filter,
        search=search,
        page=page,
        limit=limit,
    )

