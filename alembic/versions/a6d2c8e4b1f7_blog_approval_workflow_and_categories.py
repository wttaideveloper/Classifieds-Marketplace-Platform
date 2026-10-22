"""blogs and news: merchant posts with approval, categories, and a change history

Adds to cms_blog_posts: kind (blog | news), source (platform | merchant), business_name / business_logo_url, category_id,
is_live, is_pinned, pending_revision, reviewer_note / reviewed_by / reviewed_at, submitted_at, deleted_at. Adds the tables
cms_blog_categories and cms_blog_post_history.

Existing rows: kind blog, source platform. Status "archived" becomes "unpublished", soft-deleted rows get status "deleted",
and published rows are marked live. The SEO columns stay in place but are no longer used. Categories are created from the
distinct category names already on posts, so nothing loses its category.

Revision ID: a6d2c8e4b1f7
Revises: f9c3e6a2b8d4
Create Date: 2026-10-21 00:00:00.000000

"""
import uuid
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "a6d2c8e4b1f7"
down_revision = "f9c3e6a2b8d4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cms_blog_categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("name_key", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_cms_blog_categories_name_key", "cms_blog_categories", ["name_key"], unique=True)

    op.add_column("cms_blog_posts", sa.Column("kind", sa.String(10), nullable=False, server_default="blog"))
    op.add_column("cms_blog_posts", sa.Column("source", sa.String(10), nullable=False, server_default="platform"))
    op.add_column("cms_blog_posts", sa.Column("business_name", sa.String(255), nullable=True))
    op.add_column("cms_blog_posts", sa.Column("business_logo_url", sa.Text(), nullable=True))
    op.add_column("cms_blog_posts", sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("cms_blog_posts", sa.Column("is_live", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("cms_blog_posts", sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("cms_blog_posts", sa.Column("pending_revision", postgresql.JSONB(), nullable=True))
    op.add_column("cms_blog_posts", sa.Column("reviewer_note", sa.Text(), nullable=True))
    op.add_column("cms_blog_posts", sa.Column("reviewed_by", sa.String(100), nullable=True))
    op.add_column("cms_blog_posts", sa.Column("reviewed_at", sa.DateTime(), nullable=True))
    op.add_column("cms_blog_posts", sa.Column("submitted_at", sa.DateTime(), nullable=True))
    op.add_column("cms_blog_posts", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_foreign_key("fk_cms_blog_posts_category", "cms_blog_posts", "cms_blog_categories", ["category_id"], ["id"])
    op.create_index("ix_cms_blog_posts_kind", "cms_blog_posts", ["kind"])
    op.create_index("ix_cms_blog_posts_source", "cms_blog_posts", ["source"])
    op.create_index("ix_cms_blog_posts_category_id", "cms_blog_posts", ["category_id"])
    op.create_index("ix_cms_blog_posts_is_live", "cms_blog_posts", ["is_live"])
    op.create_index("ix_cms_blog_posts_submitted_at", "cms_blog_posts", ["submitted_at"])
    op.create_index("ix_cms_blog_posts_live_kind", "cms_blog_posts", ["is_live", "kind"])

    op.create_table(
        "cms_blog_post_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cms_blog_posts.id"), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.String(100), nullable=True),
        sa.Column("actor_name", sa.String(255), nullable=True),
        sa.Column("actor_role", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_cms_blog_post_history_post_id", "cms_blog_post_history", ["post_id"])
    op.create_index("ix_cms_blog_post_history_created_at", "cms_blog_post_history", ["created_at"])

    # --- existing rows
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE cms_blog_posts SET status = 'unpublished' WHERE status = 'archived' AND is_deleted = false"))
    bind.execute(sa.text("UPDATE cms_blog_posts SET status = 'deleted', deleted_at = updated_at WHERE is_deleted = true"))
    bind.execute(sa.text("UPDATE cms_blog_posts SET is_live = true WHERE status = 'published'"))

    now = datetime.utcnow()
    names = [row[0] for row in bind.execute(sa.text(
        "SELECT DISTINCT category FROM cms_blog_posts WHERE category IS NOT NULL AND btrim(category) <> ''"
    ))]
    seen = set()
    for order, name in enumerate(sorted(names, key=str.lower)):
        key = name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        bind.execute(
            sa.text(
                "INSERT INTO cms_blog_categories (id, name, name_key, is_active, sort_order, created_at) "
                "VALUES (CAST(:id AS uuid), :name, :key, true, :order, :now)"
            ),
            {"id": str(uuid.uuid4()), "name": name.strip(), "key": key, "order": order, "now": now},
        )
    bind.execute(sa.text(
        "UPDATE cms_blog_posts SET category_id = (SELECT c.id FROM cms_blog_categories c WHERE c.name_key = lower(btrim(cms_blog_posts.category))) "
        "WHERE category IS NOT NULL AND btrim(category) <> ''"
    ))


def downgrade():
    op.drop_index("ix_cms_blog_post_history_created_at", table_name="cms_blog_post_history")
    op.drop_index("ix_cms_blog_post_history_post_id", table_name="cms_blog_post_history")
    op.drop_table("cms_blog_post_history")
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE cms_blog_posts SET status = 'archived' WHERE status IN ('unpublished', 'deleted')"))
    bind.execute(sa.text("UPDATE cms_blog_posts SET status = 'draft' WHERE status NOT IN ('draft', 'published', 'archived')"))
    for name in ("ix_cms_blog_posts_live_kind", "ix_cms_blog_posts_submitted_at", "ix_cms_blog_posts_is_live",
                 "ix_cms_blog_posts_category_id", "ix_cms_blog_posts_source", "ix_cms_blog_posts_kind"):
        op.drop_index(name, table_name="cms_blog_posts")
    op.drop_constraint("fk_cms_blog_posts_category", "cms_blog_posts", type_="foreignkey")
    for column in ("deleted_at", "submitted_at", "reviewed_at", "reviewed_by", "reviewer_note", "pending_revision", "is_pinned",
                   "is_live", "category_id", "business_logo_url", "business_name", "source", "kind"):
        op.drop_column("cms_blog_posts", column)
    op.drop_index("ix_cms_blog_categories_name_key", table_name="cms_blog_categories")
    op.drop_table("cms_blog_categories")
