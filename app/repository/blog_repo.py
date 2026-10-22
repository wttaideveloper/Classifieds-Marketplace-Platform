import json
from uuid import UUID

from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Query, Session

from app.models.blog_model import BlogCategory, BlogPost, BlogPostHistory

SEARCH_COLUMNS = (BlogPost.title, BlogPost.excerpt, BlogPost.content, BlogPost.author_name, BlogPost.business_name, BlogPost.category, BlogPost.slug)


def get_post(db: Session, post_id: UUID, *, lock: bool = False) -> BlogPost | None:
    """A post that is not deleted. `lock` holds the row until commit, so two approvals cannot both act on it."""
    query = db.query(BlogPost).filter(BlogPost.id == post_id, BlogPost.is_deleted.is_(False))
    return (query.with_for_update() if lock else query).first()


def get_post_by_slug(db: Session, slug: str, include_deleted: bool = False) -> BlogPost | None:
    query = db.query(BlogPost).filter(BlogPost.slug == slug)
    if not include_deleted:
        query = query.filter(BlogPost.is_deleted.is_(False))
    return query.first()


def filter_posts(
    query: Query, *, search: str | None = None, kind: str | None = None, category: str | None = None, category_id: UUID | None = None,
    tag: str | None = None, tenant_id: UUID | None = None, enterprise_id: UUID | None = None, source: str | None = None,
) -> Query:
    if kind:
        query = query.filter(BlogPost.kind == kind)
    if source:
        query = query.filter(BlogPost.source == source)
    if category_id:
        query = query.filter(BlogPost.category_id == category_id)
    if category:
        query = query.filter(func.lower(BlogPost.category) == category.strip().lower())
    if tenant_id:
        query = query.filter(BlogPost.tenant_id == tenant_id)
    if enterprise_id:
        query = query.filter(BlogPost.enterprise_id == enterprise_id)
    if tag and tag.strip():
        # tags is a JSON list of strings: match a whole element, case-insensitively (works the same on every database)
        needle = json.dumps(tag.strip().lower()).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(func.lower(cast(BlogPost.tags, String)).like(f"%{needle}%", escape="\\"))
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(or_(*[column.ilike(term) for column in SEARCH_COLUMNS]))
    return query


def public_posts(db: Session) -> Query:
    return db.query(BlogPost).filter(BlogPost.is_live.is_(True), BlogPost.is_deleted.is_(False))


def add_history(db: Session, post: BlogPost, **fields) -> BlogPostHistory:
    row = BlogPostHistory(post_id=post.id, **fields)
    db.add(row)
    return row


def get_category(db: Session, category_id: UUID) -> BlogCategory | None:
    return db.get(BlogCategory, category_id)


def get_category_by_name(db: Session, name: str) -> BlogCategory | None:
    return db.query(BlogCategory).filter(BlogCategory.name_key == name.strip().lower()).first()


def category_names(db: Session, ids: set[UUID]) -> dict[UUID, str]:
    if not ids:
        return {}
    return {c.id: c.name for c in db.query(BlogCategory).filter(BlogCategory.id.in_(ids)).all()}
