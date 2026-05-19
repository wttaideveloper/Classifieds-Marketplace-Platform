from fastapi import APIRouter, Depends, status, Query
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.services.blog_service import public_list_blogs_service, public_blog_details_service


router = APIRouter(tags=["Public Blogs"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/blogs", status_code=status.HTTP_200_OK)
def public_list_blogs(
    categoryId: str = None,
    search: str = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return public_list_blogs_service(
        db=db,
        category_id=categoryId,
        search=search,
        page=page,
        limit=limit,
    )


@router.get("/blogs/{slug}", status_code=status.HTTP_200_OK)
def public_blog_details(
    slug: str,
    db: Session = Depends(get_db),
):
    return public_blog_details_service(db=db, slug=slug)

