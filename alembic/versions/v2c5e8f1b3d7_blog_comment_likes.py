"""Blog comment likes: one like per person per comment.

Revision ID: v2c5e8f1b3d7
Revises: u1b4d7e0a2c6

New table cms_blog_comment_likes (unique comment_id + user_id). Nothing existing changes.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "v2c5e8f1b3d7"
down_revision = "u1b4d7e0a2c6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cms_blog_comment_likes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("comment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cms_blog_comments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("comment_id", "user_id", name="uq_cms_blog_comment_likes_comment_user"),
    )
    op.create_index("ix_cms_blog_comment_likes_comment_id", "cms_blog_comment_likes", ["comment_id"])
    op.create_index("ix_cms_blog_comment_likes_user_id", "cms_blog_comment_likes", ["user_id"])


def downgrade():
    op.drop_table("cms_blog_comment_likes")
