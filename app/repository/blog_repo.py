from uuid import UUID

from sqlalchemy.orm import Session

from app.models.blog_model import BlogPost
from app.repository.query_utils import (
    apply_ilike_search,
    apply_soft_delete_filter,
    paginate_query,
)


def create_blog(db: Session, payload: dict) -> BlogPost:
    blog = BlogPost(**payload)
    db.add(blog)
    db.commit()
    db.refresh(blog)
    return blog


def get_blogs(
    db: Session,
    *,
    search: str | None = None,
    status: str | None = None,
    category: str | None = None,
    tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None,
    tag: str | None = None,
    page: int = 1,
    page_size: int = 20,
    include_deleted: bool = False,
):
    query = db.query(BlogPost)
    query = apply_soft_delete_filter(query, BlogPost, include_deleted)

    if status:
        query = query.filter(BlogPost.status == status)
    if category:
        query = query.filter(BlogPost.category == category)
    if tenant_id:
        query = query.filter(BlogPost.tenant_id == tenant_id)
    if enterprise_id:
        query = query.filter(BlogPost.enterprise_id == enterprise_id)
    if tag:
        query = query.filter(BlogPost.tags.contains([tag]))
    if search:
        query = apply_ilike_search(
            query,
            [
                BlogPost.title,
                BlogPost.excerpt,
                BlogPost.content,
                BlogPost.author_name,
                BlogPost.category,
                BlogPost.slug,
            ],
            search,
        )

    query = query.order_by(
        BlogPost.published_at.desc().nullslast(),
        BlogPost.created_at.desc(),
    )
    return paginate_query(query, page, page_size)


def get_blog_by_id(db: Session, blog_id: UUID, include_deleted: bool = False) -> BlogPost | None:
    query = db.query(BlogPost).filter(BlogPost.id == blog_id)
    if not include_deleted:
        query = apply_soft_delete_filter(query, BlogPost, include_deleted)
    return query.first()


def get_blog_by_slug(db: Session, slug: str, include_deleted: bool = False) -> BlogPost | None:
    query = db.query(BlogPost).filter(BlogPost.slug == slug)
    if not include_deleted:
        query = apply_soft_delete_filter(query, BlogPost, include_deleted)
    return query.first()


def update_blog(db: Session, blog: BlogPost, payload: dict) -> BlogPost:
    for key, value in payload.items():
        setattr(blog, key, value)
    db.commit()
    db.refresh(blog)
    return blog


def delete_blog(db: Session, blog: BlogPost) -> BlogPost:
    blog.is_deleted = True
    blog.status = "archived"
    db.commit()
    db.refresh(blog)
    return blog
