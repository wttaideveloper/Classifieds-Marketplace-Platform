"""add cms blog posts table

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-09-08 15:45:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, None] = "e3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cms_blog_posts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("enterprise_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("cover_image_url", sa.Text(), nullable=True),
        sa.Column("author_name", sa.String(length=255), nullable=True),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("seo_title", sa.String(length=255), nullable=True),
        sa.Column("seo_description", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("updated_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_cms_blog_posts_tenant_id", "cms_blog_posts", ["tenant_id"])
    op.create_index("ix_cms_blog_posts_enterprise_id", "cms_blog_posts", ["enterprise_id"])
    op.create_index("ix_cms_blog_posts_title", "cms_blog_posts", ["title"])
    op.create_index("ix_cms_blog_posts_slug", "cms_blog_posts", ["slug"], unique=True)
    op.create_index("ix_cms_blog_posts_author_id", "cms_blog_posts", ["author_id"])
    op.create_index("ix_cms_blog_posts_category", "cms_blog_posts", ["category"])
    op.create_index("ix_cms_blog_posts_status", "cms_blog_posts", ["status"])
    op.create_index("ix_cms_blog_posts_published_at", "cms_blog_posts", ["published_at"])
    op.create_index(
        "ix_cms_blog_posts_status_published",
        "cms_blog_posts",
        ["status", "published_at"],
    )
    op.create_index(
        "ix_cms_blog_posts_tenant_status",
        "cms_blog_posts",
        ["tenant_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_cms_blog_posts_tenant_status", table_name="cms_blog_posts")
    op.drop_index("ix_cms_blog_posts_status_published", table_name="cms_blog_posts")
    op.drop_index("ix_cms_blog_posts_published_at", table_name="cms_blog_posts")
    op.drop_index("ix_cms_blog_posts_status", table_name="cms_blog_posts")
    op.drop_index("ix_cms_blog_posts_category", table_name="cms_blog_posts")
    op.drop_index("ix_cms_blog_posts_author_id", table_name="cms_blog_posts")
    op.drop_index("ix_cms_blog_posts_slug", table_name="cms_blog_posts")
    op.drop_index("ix_cms_blog_posts_title", table_name="cms_blog_posts")
    op.drop_index("ix_cms_blog_posts_enterprise_id", table_name="cms_blog_posts")
    op.drop_index("ix_cms_blog_posts_tenant_id", table_name="cms_blog_posts")
    op.drop_table("cms_blog_posts")
