"""Blog engagement: likes, saved blogs (wishlist), comments with replies, reports, and the moderation history (docs/blog-engagement.md).

Only Blogs (kind = "blog") have engagement; News never does.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.database import Base


class BlogLike(Base):
    __tablename__ = "cms_blog_likes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(UUID(as_uuid=True), ForeignKey("cms_blog_posts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("post_id", "user_id", name="uq_cms_blog_likes_post_user"),)


class BlogWishlist(Base):
    __tablename__ = "cms_blog_wishlist"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(UUID(as_uuid=True), ForeignKey("cms_blog_posts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    saved_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("post_id", "user_id", name="uq_cms_blog_wishlist_post_user"), Index("ix_cms_blog_wishlist_user_saved", "user_id", "saved_at"))


class BlogComment(Base):
    __tablename__ = "cms_blog_comments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(UUID(as_uuid=True), ForeignKey("cms_blog_posts.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)            # the post's business, copied so an Enterprise Admin is checked without a join
    parent_id = Column(UUID(as_uuid=True), ForeignKey("cms_blog_comments.id", ondelete="CASCADE"), nullable=True, index=True)   # a reply's top-level comment
    author_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    author_name = Column(String(120))                                           # the display name shown publicly ("Priya S.")
    body = Column(Text, nullable=False)
    # published | held_for_review | pending_moderation | removed_by_ai | removed_by_admin | deleted_by_author
    status = Column(String(24), nullable=False, default="published", index=True)
    is_edited = Column(Boolean, nullable=False, default=False)
    edited_at = Column(DateTime)
    moderation_reason = Column(Text)                                            # why the AI held or removed it (admins only)
    moderation_labels = Column(JSONB)
    removed_by = Column(UUID(as_uuid=True))
    removed_by_role = Column(String(20))                                        # super_admin | merchant | system
    removed_reason = Column(Text)
    removed_at = Column(DateTime)
    restored_at = Column(DateTime)
    # an Enterprise Admin asked a Super Admin to look at an AI-removed comment
    review_requested_at = Column(DateTime)
    review_requested_by = Column(UUID(as_uuid=True))
    review_request_note = Column(Text)
    review_resolved_at = Column(DateTime)
    # user reports crossed the threshold: it waits in the Reported queue until a Super Admin decides
    report_triggered_at = Column(DateTime)
    report_resolved_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_cms_blog_comments_post_status", "post_id", "status"), Index("ix_cms_blog_comments_author_created", "author_id", "created_at"))


class BlogCommentReport(Base):
    __tablename__ = "cms_blog_comment_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    comment_id = Column(UUID(as_uuid=True), ForeignKey("cms_blog_comments.id", ondelete="CASCADE"), nullable=False, index=True)
    reporter_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    reason = Column(String(20), nullable=False)                                 # unsafe_medical | harassment | offensive | spam | other
    note = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("comment_id", "reporter_id", name="uq_cms_blog_comment_reports_comment_reporter"),)


class BlogCommentEvent(Base):
    """Every moderation decision on a comment: who or what, what, why, and a copy of the text judged (admins only, never public)."""

    __tablename__ = "cms_blog_comment_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    comment_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    post_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    action = Column(String(40), nullable=False, index=True)
    from_status = Column(String(24))
    to_status = Column(String(24))
    actor_id = Column(UUID(as_uuid=True))
    actor_role = Column(String(20))                                             # customer | merchant | super_admin | system
    reason = Column(Text)
    body_snapshot = Column(Text)
    detail = Column(JSONB)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
