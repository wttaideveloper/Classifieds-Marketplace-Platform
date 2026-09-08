import re
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.database import Base


def slugify(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text or str(uuid.uuid4())


class BlogPost(Base):
    __tablename__ = "cms_blog_posts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    enterprise_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    title = Column(String(255), nullable=False, index=True)
    slug = Column(String(255), nullable=False, unique=True, index=True)
    excerpt = Column(Text)
    content = Column(Text, nullable=False)
    cover_image_url = Column(Text)
    author_name = Column(String(255))
    author_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    category = Column(String(100), index=True)
    tags = Column(JSONB, default=list)

    status = Column(String(20), default="draft", nullable=False, index=True)
    # draft | published | archived

    seo_title = Column(String(255))
    seo_description = Column(Text)

    published_at = Column(DateTime, nullable=True, index=True)
    view_count = Column(Integer, default=0, nullable=False)

    is_deleted = Column(Boolean, default=False, nullable=False)
    created_by = Column(String(100))
    updated_by = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_cms_blog_posts_status_published", "status", "published_at"),
        Index("ix_cms_blog_posts_tenant_status", "tenant_id", "status"),
    )
