"""Blog governance: per-module switches with an audit trail, and the module a post belongs to.

Revision ID: h5d0f3c8e2b4
Revises: g4c9e2b7d1a3
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "h5d0f3c8e2b4"
down_revision = "g4c9e2b7d1a3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("cms_blog_posts", sa.Column("module", sa.String(12), nullable=False, server_default="general"))
    op.create_index("ix_cms_blog_posts_module", "cms_blog_posts", ["module"])
    op.create_table(
        "cms_blog_governance",
        sa.Column("module", sa.String(12), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.String(100)),
        sa.Column("updated_by_name", sa.String(255)),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "cms_blog_governance_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("module", sa.String(12), nullable=False),
        sa.Column("previous_value", sa.Boolean(), nullable=False),
        sa.Column("new_value", sa.Boolean(), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("actor_id", sa.String(100)),
        sa.Column("actor_name", sa.String(255)),
        sa.Column("actor_role", sa.String(20)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_cms_blog_governance_audit_module", "cms_blog_governance_audit", ["module"])
    op.create_index("ix_cms_blog_governance_audit_created_at", "cms_blog_governance_audit", ["created_at"])


def downgrade():
    op.drop_table("cms_blog_governance_audit")
    op.drop_table("cms_blog_governance")
    op.drop_index("ix_cms_blog_posts_module", table_name="cms_blog_posts")
    op.drop_column("cms_blog_posts", "module")
