from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.blog_model import slugify
from app.repository.blog_repo import (
    create_blog,
    delete_blog,
    get_blog_by_id,
    get_blog_by_slug,
    get_blogs,
    update_blog,
)
from app.repository.query_utils import build_pagination_meta
from app.schemas.blog_schema import (
    BlogCreate,
    BlogDetailResponse,
    BlogListItemResponse,
    BlogPaginatedResponse,
    BlogUpdate,
)


def _actor(current_user: dict | None) -> str | None:
    if not current_user:
        return None
    return str(current_user.get("id") or current_user.get("email") or "")


def _unique_slug(db: Session, base: str, exclude_id: UUID | None = None) -> str:
    candidate = slugify(base)
    suffix = 2
    while True:
        existing = get_blog_by_slug(db, candidate, include_deleted=True)
        if not existing or (exclude_id and existing.id == exclude_id):
            return candidate
        candidate = f"{slugify(base)}-{suffix}"
        suffix += 1


def _to_list_item(blog) -> dict:
    return {
        "id": blog.id,
        "title": blog.title,
        "slug": blog.slug,
        "excerpt": blog.excerpt,
        "cover_image_url": blog.cover_image_url,
        "author_name": blog.author_name,
        "category": blog.category,
        "tags": blog.tags or [],
        "status": blog.status,
        "published_at": blog.published_at,
        "view_count": blog.view_count or 0,
        "created_at": blog.created_at,
        "updated_at": blog.updated_at,
    }


def _to_detail(blog) -> dict:
    return {
        **_to_list_item(blog),
        "content": blog.content,
        "author_id": blog.author_id,
        "seo_title": blog.seo_title,
        "seo_description": blog.seo_description,
        "tenant_id": blog.tenant_id,
        "enterprise_id": blog.enterprise_id,
        "created_by": blog.created_by,
        "updated_by": blog.updated_by,
    }


def _apply_publish_side_effects(payload: dict, *, previous_status: str | None = None) -> dict:
    data = dict(payload)
    new_status = data.get("status")
    if new_status == "published" and previous_status != "published":
        data.setdefault("published_at", datetime.utcnow())
        if not data.get("published_at"):
            data["published_at"] = datetime.utcnow()
    if new_status in ("draft", "archived") and "published_at" not in data:
        # keep historical published_at unless explicitly cleared by caller
        pass
    return data


def create_blog_service(db: Session, payload: BlogCreate, current_user: dict) -> BlogDetailResponse:
    data = payload.model_dump()
    data["slug"] = _unique_slug(db, data.get("slug") or data["title"])
    data["tags"] = data.get("tags") or []
    data = _apply_publish_side_effects(data)
    data["created_by"] = _actor(current_user)
    data["updated_by"] = _actor(current_user)
    if data.get("status") == "published" and not data.get("published_at"):
        data["published_at"] = datetime.utcnow()

    blog = create_blog(db, data)
    return BlogDetailResponse.model_validate(_to_detail(blog))


def list_blogs_service(
    db: Session,
    *,
    search: str | None = None,
    status_filter: str | None = None,
    category: str | None = None,
    tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None,
    tag: str | None = None,
    page: int = 1,
    page_size: int = 20,
    public_only: bool = False,
) -> BlogPaginatedResponse:
    if public_only:
        status_filter = "published"

    items, total = get_blogs(
        db,
        search=search,
        status=status_filter,
        category=category,
        tenant_id=tenant_id,
        enterprise_id=enterprise_id,
        tag=tag,
        page=page,
        page_size=page_size,
    )
    return BlogPaginatedResponse(
        items=[BlogListItemResponse.model_validate(_to_list_item(item)) for item in items],
        pagination=build_pagination_meta(total, page, page_size),
    )


def get_blog_service(
    db: Session,
    *,
    blog_id: UUID | None = None,
    slug: str | None = None,
    public_only: bool = False,
    increment_views: bool = False,
) -> BlogDetailResponse:
    blog = None
    if blog_id:
        blog = get_blog_by_id(db, blog_id)
    elif slug:
        blog = get_blog_by_slug(db, slug)

    if not blog:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blog post not found")

    if public_only and blog.status != "published":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blog post not found")

    if increment_views and blog.status == "published":
        blog.view_count = (blog.view_count or 0) + 1
        db.commit()
        db.refresh(blog)

    return BlogDetailResponse.model_validate(_to_detail(blog))


def update_blog_service(
    db: Session,
    blog_id: UUID,
    payload: BlogUpdate,
    current_user: dict,
) -> BlogDetailResponse:
    blog = get_blog_by_id(db, blog_id, include_deleted=True)
    if not blog or blog.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blog post not found")

    data = payload.model_dump(exclude_unset=True)
    if "slug" in data and data["slug"]:
        data["slug"] = _unique_slug(db, data["slug"], exclude_id=blog.id)
    elif "title" in data and data["title"] and not data.get("slug"):
        # only auto-refresh slug if caller did not send one and title changed
        pass

    data = _apply_publish_side_effects(data, previous_status=blog.status)
    if data.get("status") == "published" and not blog.published_at and "published_at" not in data:
        data["published_at"] = datetime.utcnow()

    data["updated_by"] = _actor(current_user)
    blog = update_blog(db, blog, data)
    return BlogDetailResponse.model_validate(_to_detail(blog))


def update_blog_status_service(
    db: Session,
    blog_id: UUID,
    new_status: str,
    current_user: dict,
) -> BlogDetailResponse:
    return update_blog_service(
        db,
        blog_id,
        BlogUpdate(status=new_status),  # type: ignore[arg-type]
        current_user,
    )


def delete_blog_service(db: Session, blog_id: UUID, current_user: dict) -> None:
    blog = get_blog_by_id(db, blog_id)
    if not blog:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blog post not found")
    blog.updated_by = _actor(current_user)
    delete_blog(db, blog)
