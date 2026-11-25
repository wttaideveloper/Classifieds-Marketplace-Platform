"""Blog engagement: likes, saved blogs, comments with replies, reports and the moderation history.

Revision ID: r7c0e3a6b9d4
Revises: s8d1f4b7c0e5

Four new tables beside cms_blog_posts; nothing existing changes. Unique (post_id, user_id) on likes and saved blogs; unique (comment_id, reporter_id) on reports.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "r7c0e3a6b9d4"
down_revision = "s8d1f4b7c0e5"
branch_labels = None
depends_on = None


def _id():
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True)


def upgrade():
    op.create_table(
        "cms_blog_likes", _id(),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cms_blog_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("post_id", "user_id", name="uq_cms_blog_likes_post_user"),
    )
    op.create_index("ix_cms_blog_likes_post_id", "cms_blog_likes", ["post_id"])
    op.create_index("ix_cms_blog_likes_user_id", "cms_blog_likes", ["user_id"])

    op.create_table(
        "cms_blog_wishlist", _id(),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cms_blog_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("saved_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("post_id", "user_id", name="uq_cms_blog_wishlist_post_user"),
    )
    op.create_index("ix_cms_blog_wishlist_post_id", "cms_blog_wishlist", ["post_id"])
    op.create_index("ix_cms_blog_wishlist_user_id", "cms_blog_wishlist", ["user_id"])
    op.create_index("ix_cms_blog_wishlist_user_saved", "cms_blog_wishlist", ["user_id", "saved_at"])

    op.create_table(
        "cms_blog_comments", _id(),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cms_blog_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True)),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cms_blog_comments.id", ondelete="CASCADE")),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_name", sa.String(120)),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("is_edited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("edited_at", sa.DateTime()),
        sa.Column("moderation_reason", sa.Text()),
        sa.Column("moderation_labels", postgresql.JSONB()),
        sa.Column("removed_by", postgresql.UUID(as_uuid=True)),
        sa.Column("removed_by_role", sa.String(20)),
        sa.Column("removed_reason", sa.Text()),
        sa.Column("removed_at", sa.DateTime()),
        sa.Column("restored_at", sa.DateTime()),
        sa.Column("review_requested_at", sa.DateTime()),
        sa.Column("review_requested_by", postgresql.UUID(as_uuid=True)),
        sa.Column("review_request_note", sa.Text()),
        sa.Column("review_resolved_at", sa.DateTime()),
        sa.Column("report_triggered_at", sa.DateTime()),
        sa.Column("report_resolved_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for name, cols in (("ix_cms_blog_comments_post_id", ["post_id"]), ("ix_cms_blog_comments_tenant_id", ["tenant_id"]), ("ix_cms_blog_comments_parent_id", ["parent_id"]),
                       ("ix_cms_blog_comments_author_id", ["author_id"]), ("ix_cms_blog_comments_status", ["status"]), ("ix_cms_blog_comments_created_at", ["created_at"]),
                       ("ix_cms_blog_comments_post_status", ["post_id", "status"]), ("ix_cms_blog_comments_author_created", ["author_id", "created_at"])):
        op.create_index(name, "cms_blog_comments", cols)

    op.create_table(
        "cms_blog_comment_reports", _id(),
        sa.Column("comment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cms_blog_comments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reporter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(20), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("comment_id", "reporter_id", name="uq_cms_blog_comment_reports_comment_reporter"),
    )
    op.create_index("ix_cms_blog_comment_reports_comment_id", "cms_blog_comment_reports", ["comment_id"])
    op.create_index("ix_cms_blog_comment_reports_reporter_id", "cms_blog_comment_reports", ["reporter_id"])

    op.create_table(
        "cms_blog_comment_events", _id(),
        sa.Column("comment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("from_status", sa.String(24)),
        sa.Column("to_status", sa.String(24)),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True)),
        sa.Column("actor_role", sa.String(20)),
        sa.Column("reason", sa.Text()),
        sa.Column("body_snapshot", sa.Text()),
        sa.Column("detail", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for name, cols in (("ix_cms_blog_comment_events_comment_id", ["comment_id"]), ("ix_cms_blog_comment_events_post_id", ["post_id"]),
                       ("ix_cms_blog_comment_events_action", ["action"]), ("ix_cms_blog_comment_events_created_at", ["created_at"])):
        op.create_index(name, "cms_blog_comment_events", cols)


def downgrade():
    for table in ("cms_blog_comment_events", "cms_blog_comment_reports", "cms_blog_comments", "cms_blog_wishlist", "cms_blog_likes"):
        op.drop_table(table)
