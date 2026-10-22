import re
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.database import Base


def slugify(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text or str(uuid.uuid4())


class BlogCategory(Base):
    """A category for blog and news posts. Switched off rather than deleted once posts use it."""
    __tablename__ = "cms_blog_categories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    name_key = Column(String(100), nullable=False, unique=True, index=True)   # lower-cased name, so "Health" and "health" clash
    is_active = Column(Boolean, default=True, nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BlogPost(Base):
    __tablename__ = "cms_blog_posts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    enterprise_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    # blog | news. Both use the same form; the kind decides which list the post shows in.
    kind = Column(String(10), default="blog", nullable=False, index=True)
    # platform (written by a Super Admin) | merchant (written by a business, needs approval)
    source = Column(String(10), default="platform", nullable=False, index=True)

    title = Column(String(255), nullable=False, index=True)
    slug = Column(String(255), nullable=False, unique=True, index=True)
    excerpt = Column(Text)                       # the short summary shown on the list card
    content = Column(Text, nullable=False)       # the body (formatted text)
    cover_image_url = Column(Text)
    author_name = Column(String(255))
    author_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    business_name = Column(String(255))
    business_logo_url = Column(Text)

    category = Column(String(100), index=True)   # the category's name when the post was last saved
    category_id = Column(UUID(as_uuid=True), ForeignKey("cms_blog_categories.id"), nullable=True, index=True)
    tags = Column(JSONB, default=list)

    # draft | pending_approval | changes_requested | rejected | published | unpublished | deleted
    status = Column(String(20), default="draft", nullable=False, index=True)
    # True while the public can read this post. A published post that its merchant edits goes back to pending_approval
    # but stays live with the version below until the edit is approved.
    is_live = Column(Boolean, default=False, nullable=False, index=True)
    is_pinned = Column(Boolean, default=False, nullable=False)
    # The merchant's unapproved edits of a live post: {title, excerpt, content, cover_image_url, category_id, category, tags, kind}.
    pending_revision = Column(JSONB, nullable=True)

    reviewer_note = Column(Text)                 # the reason the Super Admin gave (changes requested, rejected, taken down)
    reviewed_by = Column(String(100))
    reviewed_at = Column(DateTime, nullable=True)
    submitted_at = Column(DateTime, nullable=True, index=True)

    # Kept for rows written before SEO tools were left out of scope; nothing reads or writes them any more.
    seo_title = Column(String(255))
    seo_description = Column(Text)

    published_at = Column(DateTime, nullable=True, index=True)
    view_count = Column(Integer, default=0, nullable=False)

    is_deleted = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime, nullable=True)
    created_by = Column(String(100))
    updated_by = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_cms_blog_posts_status_published", "status", "published_at"),
        Index("ix_cms_blog_posts_tenant_status", "tenant_id", "status"),
        Index("ix_cms_blog_posts_live_kind", "is_live", "kind"),
    )


class BlogPostHistory(Base):
    """Every change to a post's status or content, with who did it and why. Rows are never edited or deleted."""
    __tablename__ = "cms_blog_post_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(UUID(as_uuid=True), ForeignKey("cms_blog_posts.id"), nullable=False, index=True)
    action = Column(String(30), nullable=False)
    # created | edited | submitted | approved | changes_requested | rejected | published | unpublished | deleted | pinned | unpinned
    from_status = Column(String(20))
    to_status = Column(String(20))
    reason = Column(Text)
    actor_id = Column(String(100))
    actor_name = Column(String(255))
    actor_role = Column(String(20))              # super_admin | merchant
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
