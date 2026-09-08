from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_super_admin
from app.db.database import get_db
from app.schemas.blog_schema import (
    BlogCreate,
    BlogDetailResponse,
    BlogPaginatedResponse,
    BlogStatusUpdate,
    BlogUpdate,
)
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.services.blog_service import (
    create_blog_service,
    delete_blog_service,
    get_blog_service,
    list_blogs_service,
    update_blog_service,
    update_blog_status_service,
)

router = APIRouter(tags=["CMS — Blogs"])


# ---- Public (published only) ----


@router.get(
    "",
    response_model=BlogPaginatedResponse,
    summary="List published blog posts (public)",
)
def list_public_blogs(
    search: str | None = Query(None),
    category: str | None = Query(None),
    tag: str | None = Query(None),
    tenant_id: UUID | None = Query(None),
    enterprise_id: UUID | None = Query(None),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
):
    return list_blogs_service(
        db,
        search=search,
        category=category,
        tag=tag,
        tenant_id=tenant_id,
        enterprise_id=enterprise_id,
        page=page,
        page_size=page_size,
        public_only=True,
    )


# ---- Admin / Super Admin CMS (must be registered before /{slug}) ----


@router.get(
    "/admin/posts",
    response_model=BlogPaginatedResponse,
    summary="List all blog posts (CMS admin)",
    description="Includes draft / published / archived. Requires admin or super_admin.",
)
def list_admin_blogs(
    search: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    category: str | None = Query(None),
    tag: str | None = Query(None),
    tenant_id: UUID | None = Query(None),
    enterprise_id: UUID | None = Query(None),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_super_admin),
):
    return list_blogs_service(
        db,
        search=search,
        status_filter=status_filter,
        category=category,
        tag=tag,
        tenant_id=tenant_id,
        enterprise_id=enterprise_id,
        page=page,
        page_size=page_size,
        public_only=False,
    )


@router.post(
    "/admin/posts",
    response_model=BlogDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create blog post (CMS admin)",
)
def create_admin_blog(
    payload: BlogCreate = Body(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_super_admin),
):
    return create_blog_service(db, payload, current_user)


@router.get(
    "/admin/posts/{blog_id}",
    response_model=BlogDetailResponse,
    summary="Get blog post by ID (CMS admin)",
)
def get_admin_blog(
    blog_id: UUID = Path(...),
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_super_admin),
):
    return get_blog_service(db, blog_id=blog_id, public_only=False)


@router.put(
    "/admin/posts/{blog_id}",
    response_model=BlogDetailResponse,
    summary="Update blog post (CMS admin)",
)
def update_admin_blog(
    blog_id: UUID,
    payload: BlogUpdate = Body(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_super_admin),
):
    return update_blog_service(db, blog_id, payload, current_user)


@router.patch(
    "/admin/posts/{blog_id}/status",
    response_model=BlogDetailResponse,
    summary="Update blog status (draft|published|archived)",
)
def update_admin_blog_status(
    blog_id: UUID,
    payload: BlogStatusUpdate = Body(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_super_admin),
):
    return update_blog_status_service(db, blog_id, payload.status, current_user)


@router.delete(
    "/admin/posts/{blog_id}",
    status_code=status.HTTP_200_OK,
    summary="Soft-delete blog post (CMS admin)",
)
def delete_admin_blog(
    blog_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_super_admin),
):
    delete_blog_service(db, blog_id, current_user)
    return {"message": "Blog post archived successfully"}


@router.get(
    "/{slug}",
    response_model=BlogDetailResponse,
    summary="Get published blog by slug (public)",
)
def get_public_blog_by_slug(
    slug: str = Path(..., description="Blog URL slug"),
    db: Session = Depends(get_db),
):
    return get_blog_service(db, slug=slug, public_only=True, increment_views=True)
